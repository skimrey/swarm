"""Does the pool now receive project context, and only when it is relevant?

The failure this guards against is not "no context" -- it is context attached
to prompts that had no use for it, which inflates num_ctx and makes Ollama
reload the model between components.
"""
import team


def main():
    # A request that plainly concerns the documented subject matter.
    relevant = team.project_context(
        "route a prompt to the right ollama node based on measured throughput "
        "and model capability across the swarm")
    assert relevant, "context store returned nothing for an on-topic request"
    assert "Project context" in relevant
    print("on-topic request: %d chars of context attached" % len(relevant))
    first = relevant.strip().splitlines()[2:4]
    for line in first:
        print("   | %s" % line[:72])

    # A request with nothing to do with this project.
    off = team.project_context(
        "write a function that converts celsius to fahrenheit")
    assert off == "", "irrelevant card dragged in: %r" % off[:200]
    print("off-topic request: nothing attached, as intended")

    # Bounded. A card cannot blow up a component prompt.
    big = team.project_context(
        "ollama node model routing throughput context swarm topology "
        "capability latency budget", budget_tokens=600)
    import swarm
    assert swarm.est_tokens(big) < 1200, (
        "context exceeded its budget: %d tokens" % swarm.est_tokens(big))
    print("budget respected: %d tokens for a maximally matching query"
          % swarm.est_tokens(big))

    # Never raises, whatever it is handed.
    for weird in ("", "   ", "\x00", "a" * 5000):
        team.project_context(weird)
    print("degenerate inputs: no exception")
    print("OK")


if __name__ == "__main__":
    main()
