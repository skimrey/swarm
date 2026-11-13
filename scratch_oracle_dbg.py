"""Show exactly what the test-writing model returns, and where it is rejected."""
import checks
import codegen
import swarm
import team

SPEC = ("edit_script(a: str, b: str) -> list[tuple[str, str]] returns a "
        "MINIMAL character-level edit script as a list of (op, char) pairs "
        "where op is 'keep', 'insert' or 'delete', such that applying it left "
        "to right to a produces b, and the number of non-keep operations "
        "equals len(a) + len(b) - 2 * the length of the longest common "
        "subsequence of a and b.")

cfg = swarm.Config.load()
state = swarm.State()
node, model = team.pick_solvers(cfg, state, 1, count=4, family="gemma",
                                max_over=1)[0]
prompt = checks.TEST_PROMPT.format(spec=SPEC)
ctx = swarm.pick_num_ctx(cfg, node, model, swarm.est_tokens(prompt), 700,
                         reasoning=False)
print("node=%s/%s prompt_tokens=%d ctx=%d"
      % (node.name, model, swarm.est_tokens(prompt), ctx))

with swarm.node_claim(state, node, model):
    r = swarm.chat_complete(node, model,
                            [{"role": "user", "content": prompt}],
                            num_ctx=ctx, answer_tokens=700, headroom=1.8,
                            think=False, temperature=0.1, timeout=420)

raw = r.get("raw") or {}
print("done_reason=%r thinking_chars=%d answer_chars=%d"
      % (raw.get("done_reason"), len(r.get("thinking") or ""),
         len(r.get("text") or "")))
print("\n===== RAW TEXT =====")
print(r["text"])
print("===== END =====\n")

extracted = codegen.extract_code(r["text"])
print("extract_code -> %s" % ("None" if extracted is None
                              else "%d chars" % len(extracted)))
if extracted:
    body = checks._strip_scaffolding(extracted)
    print("after stripping -> %d chars, has check(): %s"
          % (len(body), "check(" in body or "check_raises(" in body))
    source = checks._HEAD % {"path": "x.py", "module": "x.py"}
    err = codegen.gate_syntax(source + body + checks._TAIL, "t.py")
    print("syntax gate -> %s" % (err.splitlines()[0] if err else "clean"))
state.close()
