"""Offline tests for the axes and the compiled combinations behind briefs."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intent

AXES = [("width", True, ["a", "b", "c", "d", "e"]), ("gap", True, ["v", "w", "x", "y", "z"]),
        ("motif", False, ["stairs", "zig", "wave", "pillars", "checker", "spiral"]),
        ("deco", False, ["none", "p", "q", "r", "s"])]


class BriefTests(unittest.TestCase):
    def test_parse_axes(self):
        theme, axes = intent.parse_axes(
            "THEME: tidy things\nRAMP size: 1 | 2 | 3\nFREE kind: x | y | z | w\nRAMP name: a | b | c\nRAMP short: a | b\njunk")
        self.assertEqual(theme, "tidy things")
        self.assertEqual([(n, r) for n, r, _ in axes], [("size", True), ("kind", False)])

    def test_every_combination_is_unique(self):
        for count in (10, 40, 120):
            combos = intent.compile_qualities(AXES, count)
            self.assertEqual(len({tuple(v for _, v in c) for c in combos}), count)

    def test_ramp_climbs_and_free_rotates(self):
        combos = intent.compile_qualities(AXES, 40)
        self.assertEqual(combos[0][0][1], "a")
        self.assertEqual(combos[-1][0][1], "e")
        self.assertEqual(len({c[2][1] for c in combos[:6]}), 6)  # a free axis does not repeat in a window

    def test_same_input_same_output(self):
        self.assertEqual(intent.compile_qualities(AXES, 40), intent.compile_qualities(AXES, 40))


if __name__ == "__main__":
    unittest.main()
