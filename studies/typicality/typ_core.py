"""Typicality logic with no web framework: the study file, assets, the session id, one session.

A session rates every stimulus once, plus every attention item, in the order of order.py.
The order depends only on the study and the session id, so a session can be rebuilt after a
server restart by replaying its recorded ratings.
"""

import csv
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from order import even_order

# <user_id>--run=<k>; user_id as in the chain study (single hyphens allowed, never "--")
SID_RE = re.compile(r"(?P<user>[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)--run=(?P<run>[0-9]{1,6})")

STUDY_KEYS = {"study_id", "stimuli_name", "assets_root", "question", "min_value", "max_value", "step",
              "left_label", "right_label", "completion_code", "potato_config", "output_root", "host", "port",
              "threads", "no_consent_code", "abandon_after_min", "n_attention"}


def parse_sid(sid: str):
    """(user_id, run) from a session id, or None if it is malformed."""
    m = SID_RE.fullmatch(sid or "")
    return (m["user"], int(m["run"])) if m else None


def load_study(path) -> dict:
    study = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    study.setdefault("step", 1)
    missing, unknown = STUDY_KEYS - set(study), set(study) - STUDY_KEYS
    if missing or unknown:
        raise ValueError(f"{path}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}")
    if type(study["n_attention"]) is not int or study["n_attention"] < 0:
        raise ValueError(f"{path}: n_attention must be an integer >= 0")
    lo, hi, step = study["min_value"], study["max_value"], study["step"]
    if not lo < hi or not step > 0 or not _on_grid(hi, lo, step):
        raise ValueError(f"{path}: need min_value < max_value and (max_value - min_value) a multiple of step")
    return study


def _on_grid(v, lo, step) -> bool:
    k = (v - lo) / step
    return math.isclose(k, round(k), abs_tol=1e-6)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Assets:
    stimuli: dict      # stimulus_id (int) -> text
    attention: list    # [{att_id, stimulus, answer: min|max}]
    sha256: dict       # file name -> sha256


def load_assets(assets_root, stimuli_name: str) -> Assets:
    folder = Path(assets_root) / stimuli_name
    stim_path, att_path = folder / "stimuli.csv", folder / "attention_checks_typicality.csv"
    stimuli = {}
    with open(stim_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sid, text = int(row["stimulus_id"]), row["stimulus"].strip()
            if not text or sid in stimuli:
                raise ValueError(f"{stim_path}: empty or duplicate stimulus (id {sid})")
            stimuli[sid] = text
    if len(set(stimuli.values())) != len(stimuli):
        raise ValueError(f"{stim_path}: duplicate stimulus texts")
    with open(att_path, newline="", encoding="utf-8") as f:   # att_id, stimulus, answer (min|max)
        attention = [{"att_id": r["att_id"].strip(), "stimulus": (r["stimulus"] or "").strip(), "answer": r["answer"].strip()}
                     for r in csv.DictReader(f)]
    if len({a["att_id"] for a in attention}) != len(attention):
        raise ValueError(f"{att_path}: duplicate att_id")
    for a in attention:
        if a["answer"] not in ("min", "max") or not a["stimulus"] or not a["att_id"]:
            raise ValueError(f"{att_path}: bad row {a}")
    return Assets(stimuli, attention, {p.name: sha256_file(p) for p in (stim_path, att_path)})


class Session:
    """``items`` is the fixed order; ``i_trial`` the next one to rate."""

    def __init__(self, study: dict, assets: Assets, sid: str):
        self.study, self.sid = study, sid
        order = even_order(list(assets.stimuli), assets.attention, study["n_attention"], f"{study['study_id']}\x1f{sid}")
        self.items = [{"trial_type": "attention", "att_id": x["att_id"], "stimulus_id": None, "text": x["stimulus"],
                       "answer": x["answer"]} if kind == "attention" else
                      {"trial_type": "stimulus", "att_id": None, "stimulus_id": x, "text": assets.stimuli[x]}
                      for kind, x in order]
        self.i_trial = 0

    @property
    def n_trials(self) -> int:
        return len(self.items)

    @property
    def done(self) -> bool:
        return self.i_trial >= len(self.items)

    def valid_value(self, value) -> bool:
        lo, hi = self.study["min_value"], self.study["max_value"]
        return (type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi
                and _on_grid(value, lo, self.study["step"]))

    def rate(self, value) -> dict:
        """Record the rating of the current item and move on; returns the record."""
        if self.done:
            raise ValueError("session already complete")
        if not self.valid_value(value):
            raise ValueError("value outside the scale or off its step")
        it = self.items[self.i_trial]
        correct = None
        if it["trial_type"] == "attention":
            end = self.study["max_value"] if it["answer"] == "max" else self.study["min_value"]
            correct = math.isclose(value, end, abs_tol=1e-9)
        rec = {"i_trial": self.i_trial, "n_trials": self.n_trials, "trial_type": it["trial_type"],
               "stimulus_id": it["stimulus_id"], "att_id": it["att_id"], "value": value, "correct": correct}
        self.i_trial += 1
        return rec

    def skip(self) -> dict:
        """Skip the current item ("Feeling uncomfortable with this item?") and move on.
        Stimulus: returns the skip event {i_trial, stimulus_id} (not a rating). Attention item:
        answered as incorrect, returns its record (value None, skipped True)."""
        if self.done:
            raise ValueError("session already complete")
        it = self.items[self.i_trial]
        if it["trial_type"] == "attention":
            out = {"i_trial": self.i_trial, "n_trials": self.n_trials, "trial_type": "attention",
                   "stimulus_id": None, "att_id": it["att_id"], "value": None, "correct": False, "skipped": True}
        else:
            out = {"i_trial": self.i_trial, "stimulus_id": it["stimulus_id"]}
        self.i_trial += 1
        return out

    def replay(self, records: list, skips: list = ()):
        """Re-apply recorded ratings and skips (after a restart), checking they belong to this order."""
        by_trial = {s["i_trial"]: ("skip", s) for s in skips}
        by_trial.update({r["i_trial"]: ("rec", r) for r in records})
        for i in sorted(by_trial):
            kind, r = by_trial[i]
            it = None if self.done else self.items[self.i_trial]
            if it is None or i != self.i_trial or r["stimulus_id"] != it["stimulus_id"] \
                    or r.get("att_id", it["att_id"]) != it["att_id"]:
                raise ValueError(f"replay mismatch at trial {i}: assets or code changed since the session started")
            if kind == "skip" or r.get("skipped"):
                self.skip()
            else:
                self.rate(r["value"])
