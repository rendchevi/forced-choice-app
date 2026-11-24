"""Tests for the per-participant item order.   python -m unittest discover -s tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from order import even_order

STIM = [str(i) for i in range(23)]
ATT = [f"att{i}" for i in range(6)]


class TestOrder(unittest.TestCase):
    def test_everything_once(self):
        o = even_order(STIM, ATT, "s\x1fu1")
        self.assertEqual(sorted(o), sorted(STIM + ATT))

    def test_attention_evenly_spread(self):
        o = even_order(STIM, ATT, "s\x1fu1")
        positions = [i for i, x in enumerate(o) if x in ATT]
        stimuli_before = [p - k for k, p in enumerate(positions)]
        self.assertEqual(stimuli_before, [3, 6, 9, 13, 16, 19])   # floor(k*23/7)

    def test_reproducible_and_differs_between_sessions(self):
        self.assertEqual(even_order(STIM, ATT, "s\x1fu1"), even_order(list(reversed(STIM)), ATT, "s\x1fu1"))
        self.assertNotEqual(even_order(STIM, ATT, "s\x1fu1"), even_order(STIM, ATT, "s\x1fu2"))

    def test_no_attention(self):
        self.assertEqual(sorted(even_order(STIM, [], "x")), sorted(STIM))


if __name__ == "__main__":
    unittest.main()
