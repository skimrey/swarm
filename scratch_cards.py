"""A relevant card must arrive whole, or its rules never reach a writer.

The budget was 600 tokens, so any card longer than that could only ever be
attached as its one-line summary no matter how well it scored. Measured:
`policy-api-writes` scored 11.5 on the request it was written for and
arrived as 69 tokens of abstract, with every rule in it dropped. Silently
degrading the best match is the worst available failure, because it looks
exactly like the card being attached.
"""
import swarm
import team

WRITES = "policy-api-writes"
URLS = "platform-maven-urls"

FIRE = [
    "write a script that creates an applet, creates a project, assigns the "
    "applet to the project, and creates a test task",
    "write me a script that uploads a dummy unit to the task",
    "push it to the platform as a new applet and give me the project link",
    "create a user and return the id",
]

QUIET = [
    "write a function that reverses a linked list",
    "slugify a string, lowercase and hyphens only",
    "make the shapes dance faster and more colourfully",
    "write a bytecode vm and an assembler for it",
]


def tiers(request):
    packed = swarm.pack_context(request, team.CONTEXT_BUDGET)
    return {m["id"]: m["tier"] for m in packed["cards"]}


def test_every_card_can_arrive_whole():
    """The budget has to fit the cards that exist, not the other way round.

    Asserted against the largest card rather than a fixed number, so
    writing a longer one later fails here instead of silently degrading
    in a live run.
    """
    biggest = max(swarm.load_cards(swarm.CONTEXT_DIR),
                  key=lambda c: c.tokens("full"))
    assert biggest.tokens("full") <= team.CONTEXT_BUDGET, (
        "%s needs %d tokens, budget is %d"
        % (biggest.id, biggest.tokens("full"), team.CONTEXT_BUDGET))
    print("  the largest card (%s, %d tok) fits in %d"
          % (biggest.id, biggest.tokens("full"), team.CONTEXT_BUDGET))


def test_the_write_rules_arrive_in_full_not_as_a_summary():
    for request in FIRE:
        got = tiers(request)
        assert got.get(WRITES) == "full", (request, got)
    print("  %d write requests get the rules in full" % len(FIRE))


def test_two_relevant_cards_both_fit():
    """A request naming a platform link and a create wants both."""
    got = tiers(FIRE[2])
    assert got.get(WRITES) == "full" and got.get(URLS) == "full", got
    print("  a request needing two cards gets both whole")


def test_raising_the_budget_did_not_loosen_the_gate():
    """Fitting more cards and attaching irrelevant ones are different
    questions. The second is the relevance gate's, and it must be
    untouched by the first."""
    for request in QUIET:
        assert tiers(request) == {}, (request, tiers(request))
        assert team.project_context(request) == "", request
    print("  %d unrelated requests still get nothing" % len(QUIET))


def test_the_rules_themselves_are_present():
    """Reading the tier proves a card arrived; this proves what is in it."""
    text = team.project_context(FIRE[1])
    for phrase in ("Ask the clock, never write a date", "time.strftime",
                   "unique across the whole service", "retry once",
                   "response body"):
        assert phrase in text, phrase
    print("  the clock rule, the retry rule and the body rule all arrive")


def main():
    test_every_card_can_arrive_whole()
    test_the_write_rules_arrive_in_full_not_as_a_summary()
    test_two_relevant_cards_both_fit()
    test_raising_the_budget_did_not_loosen_the_gate()
    test_the_rules_themselves_are_present()
    print("OK")


if __name__ == "__main__":
    main()
