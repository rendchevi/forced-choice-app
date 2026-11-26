"""Tests for the per-participant item order.   python -m unittest discover -s tests -v"""

import random
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from order import even_order, pick_attention

STIM = [str(i) for i in range(23)]
ATT = [{"att_id": f"att{i}", "stimulus": f"check {i}", "answer": "max"} for i in range(6)]


def att_ids(order):
    return [x["att_id"] for kind, x in order if kind == "attention"]


class TestOrder(unittest.TestCase):
    def test_every_stimulus_once_and_k_checks(self):
        o = even_order(STIM, ATT, 6, "s\x1fu1")
        self.assertEqual(sorted(x for kind, x in o if kind == "stimulus"), sorted(STIM))
        self.assertEqual(sorted(att_ids(o)), sorted(a["att_id"] for a in ATT))

    def test_attention_evenly_spread(self):
        o = even_order(STIM, ATT, 6, "s\x1fu1")
        positions = [i for i, (kind, _) in enumerate(o) if kind == "attention"]
        self.assertEqual([p - k for k, p in enumerate(positions)], [3, 6, 9, 13, 16, 19])   # floor(k*23/7)

    def test_fewer_checks_than_items(self):
        ids = att_ids(even_order(STIM, ATT, 2, "s\x1fu1"))
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_more_checks_than_items_reused_evenly(self):
        for key in ("s\x1fu1", "s\x1fu2", "s\x1fu3"):
            ids = att_ids(even_order(STIM, ATT, 14, key))
            self.assertEqual(len(ids), 14)
            self.assertEqual(set(ids), {a["att_id"] for a in ATT})
            self.assertLessEqual(max(Counter(ids).values()), 3)                 # ceil(14/6)
            self.assertTrue(all(a != b for a, b in zip(ids, ids[1:])))

    def test_pick_attention_never_repeats_back_to_back(self):
        items = ATT[:2]
        for k in range(12):
            for seed in range(20):
                got = [a["att_id"] for a in pick_attention(items, k, random.Random(seed))]
                self.assertEqual(len(got), k)
                self.assertTrue(all(a != b for a, b in zip(got, got[1:])), got)
        with self.assertRaises(ValueError):
            pick_attention([], 1, random.Random(0))

    def test_reproducible_and_differs_between_sessions(self):
        self.assertEqual(even_order(STIM, ATT, 6, "s\x1fu1"), even_order(list(reversed(STIM)), ATT[::-1], 6, "s\x1fu1"))
        self.assertNotEqual(even_order(STIM, ATT, 6, "s\x1fu1"), even_order(STIM, ATT, 6, "s\x1fu2"))

    def test_no_attention(self):
        self.assertEqual(sorted(x for _, x in even_order(STIM, ATT, 0, "x")), sorted(STIM))


if __name__ == "__main__":
    unittest.main()
