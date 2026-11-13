"""Where do the tokens actually go when the 12b writes edit_script?

"It truncated" and "it rambled" look identical from outside the call. This
asks for one generation with a deliberately huge cap and then measures how the
output splits between code and everything else, which decides whether the cap
is too small or the model is filling whatever space it is given.
"""
import re

import codegen
import swarm

SPEC = ("edit_script(a: str, b: str) -> list[tuple[str, str]] returns a "
        "MINIMAL character-level edit script as a list of (op, char) pairs "
        "where op is 'keep', 'insert' or 'delete', such that applying it left "
        "to right to a produces b, and the number of non-keep operations "
        "equals len(a) + len(b) - 2 * the length of the longest common "
        "subsequence of a and b. This requires a dynamic-programming LCS "
        "table with backtracking. Define only edit_script and any helpers it "
        "needs.")

cfg = swarm.Config.load()
state = swarm.State()
node = [n for n in cfg.nodes if n.name == "g26b"][0]
model = "gemma4:12b"

prompt = codegen.GENERATE_PROMPT.format(spec=SPEC, extra="")
print("prompt_tokens=%d, asking for up to 8000 output tokens"
      % swarm.est_tokens(prompt))

with swarm.node_claim(state, node, model):
    r = swarm.chat_complete(node, model, [{"role": "user", "content": prompt}],
                            num_ctx=16384, answer_tokens=8000, headroom=1.05,
                            think=False, temperature=0.1, timeout=900)

raw = r.get("raw") or {}
text = r["text"] or ""
print("done_reason=%r output_tokens=%s thinking_chars=%d answer_chars=%d"
      % (raw.get("done_reason"), raw.get("eval_count"),
         len(r.get("thinking") or ""), len(text)))

fences = re.findall(r"```", text)
print("fence markers seen: %d (need 2 for one complete block)" % len(fences))

blocks = codegen.FENCE.findall(text)
code = max(blocks, key=len) if blocks else codegen.extract_code(text)
print("\ncode recovered: %d chars, %d lines"
      % (len(code), len(code.splitlines())))
print("prose/other:    %d chars" % (len(text) - len(code)))

print("\n===== FIRST 700 CHARS =====")
print(text[:700])
print("\n===== LAST 700 CHARS =====")
print(text[-700:])
state.close()
