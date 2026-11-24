"""Merge every session of a study into two CSVs for analysis.

    python export.py [sessions.yaml]

Writes {output_root}/{study_id}/export--trials.csv (one row per answered trial, raw ids)
and export--sessions.csv (one row per session). Both are derived from the session
folders and are rewritten on every run.
"""

import csv
import json
import sys
from pathlib import Path

from chain_core import load_study, session_files

TRIAL_COLS = ["user_id", "sid", "i_trial", "trial_type", "chain_idx", "i_draw", "n_draw", "n_chain", "x_id", "x_star_id", "x_hat_id",
              "left_id", "chosen_side", "proposal_kind", "n_steps", "n_rejected", "att_id", "correct", "rt_ms", "server_ms", "t_prev", "t_answered"]


def main(study_path="sessions.yaml"):
    study, _ = load_study(study_path)
    root = Path(study["output_root"]) / study["study_id"]
    if not root.is_dir():
        sys.exit(f"no data yet: {root} does not exist")
    sessions, trials = [], []
    for meta_path in session_files(root):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        spec = meta.pop("spec")
        meta.pop("asset_sha256")
        meta.update({f"prolific_{k}": v for k, v in (meta.pop("prolific", None) or {}).items()})
        sessions.append({**meta, **{f"spec_{k}": v for k, v in spec.items()}})
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
