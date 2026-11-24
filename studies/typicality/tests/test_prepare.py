"""CPU-only tests for prepare.py.   python -m unittest discover -s tests -v"""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prepare


class TestPrepare(unittest.TestCase):
    def test_js_number_matches_browser_range_values(self):
        self.assertEqual([prepare.js_number(v) for v in (0, 100, 100.0, -5, 0.5, 2.25)],
                         ["0", "100", "100", "-5", "0.5", "2.25"])

    def test_attention_items_use_the_configured_ends(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            p.write_text(json.dumps([{"att_id": "a1", "stimulus": "go right", "answer": "max"},
                                     {"att_id": "a2", "stimulus": "go left", "answer": "min"}]))
            items = prepare.attention_items(p, {"min_value": 1, "max_value": 7.5})
            self.assertEqual([i["expected_answer"] for i in items], [{"typicality": "7.5"}, {"typicality": "1"}])
            self.assertEqual(items[0]["stimulus"], "go right")       # shown in the stimulus slot
            p.write_text(json.dumps([{"att_id": "a1", "stimulus": "x", "answer": "middle"}]))
            with self.assertRaises(ValueError):
                prepare.attention_items(p, {"min_value": 0, "max_value": 1})

    def test_bad_stimuli_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.csv"
            for rows in (["1,a,x", "1,b,x"], ["1,a,x", "2,a,x"], ["1, ,x"]):
                p.write_text("stimulus_id,stimulus,ref_label\n" + "\n".join(rows) + "\n")
                with self.assertRaises(ValueError, msg=rows):
                    prepare.check_stimuli(p)

    def test_main_writes_a_config_potato_accepts(self):
        with tempfile.TemporaryDirectory() as d:
            study = prepare.main("study.yaml", out=Path(d))
            with open(Path(d) / "data" / "stimuli.csv", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            att = json.loads((Path(d) / "data" / "attention.json").read_text())
            ids = [r["stimulus_id"] for r in rows]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue({a["id"] for a in att} <= set(ids))  # every participant gets every check
            self.assertEqual(json.dumps(att[0]["expected_answer"]) in
                             (json.dumps({"typicality": prepare.js_number(study["min_value"])}),
                              json.dumps({"typicality": prepare.js_number(study["max_value"])})), True)
            r = subprocess.run(["potato", "validate", "config.yaml", "--strict"], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
