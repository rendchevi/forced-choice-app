"""Merge every session of the study into two CSVs for analysis.

    python export.py [study.yaml]

Writes {output_root}/{study_id}/export--trials.csv (one row per rating, raw ids) and
export--sessions.csv (one row per session). Both are derived from the session folders and
are rewritten on every run.
"""

import csv
import json
import sys
from pathlib import Path

from typ_core import load_study

TRIAL_COLS = ["user_id", "sid", "i_trial", "n_trials", "trial_type", "stimulus_id", "att_id", "value", "correct",
              "rt_ms", "first_touch_ms", "server_ms", "t_answered"]


def main(study_path="study.yaml"):
    study = load_study(study_path)
    root = Path(study["output_root"]) / study["study_id"]
    if not root.is_dir():
        sys.exit(f"no data yet: {root} does not exist")
    sessions, trials = [], []
    for meta_path in sorted(root.glob("*/*/session.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        scale = meta.pop("scale")
        meta.pop("asset_sha256")
        sessions.append({**meta, **scale})
        for line in (meta_path.parent / "trials.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                trials.append(json.loads(line))
    for name, rows, cols in [("export--trials.csv", trials, TRIAL_COLS),
                             ("export--sessions.csv", sessions, list(sessions[0]) if sessions else [])]:
        with open(root / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"{root / name}: {len(rows)} rows")


if __name__ == "__main__":
    main(*sys.argv[1:])
