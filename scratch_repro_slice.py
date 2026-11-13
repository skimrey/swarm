import openapi

spec = openapi.load(openapi.resolve("maven"))
req = "create a project; save its id to output.json"

# First: what does term-matching alone (no model hints) select?
text, chosen = openapi.slice_for(spec, req, limit=25, hints=[], avoid=[],
                                 exclude_tags=openapi.excluded_tags("maven"))
print("=== slice_for with NO hints (pure term scoring + closures) ===")
for e in chosen:
    print(e["method"], e["path"])
