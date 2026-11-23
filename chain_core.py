"""Chain logic with no web framework: specs, assets, the session id, and one session's trials.

A session is fully determined by its seed and the sides the participant chose, so a
session can be rebuilt after a server restart by replaying its recorded choices.
"""

import csv
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import yaml

from proposal import load_proposal_class

SIDES = ("left", "right")

# <user_id>--spec=<spec>--chain=<k>; user_id may contain single hyphens, never "--"
SID_RE = re.compile(r"(?P<user>[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)"
                    r"--spec=(?P<spec>[A-Za-z0-9_]+)--chain=(?P<chain>[0-9]{1,6})")


def parse_sid(sid: str):
    """(user_id, spec_name, chain) from a session id, or None if it is malformed."""
    m = SID_RE.fullmatch(sid or "")  # fullmatch: "$" would accept a trailing newline
    return (m["user"], m["spec"], int(m["chain"])) if m else None


def session_seed(study_id: str, user_id: str, spec_name: str, chain: int) -> int:
    key = "\x1f".join([study_id, user_id, spec_name, str(chain)]).encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def attention_after(n_draw: int, n_attention: int) -> list[int]:
    """Attention item k (1..K) goes after draw floor(k * n_draw / (K + 1))."""
    return [k * n_draw // (n_attention + 1) for k in range(1, n_attention + 1)]


# ---------------------------------------------------------------- specs and assets

@dataclass(frozen=True)
class Spec:
    name: str
    stimuli_name: str
    graph_name: str
    propose_fn: str
    n_draw: int
    question: str
    n_attention: int
    attention_pass: int
    propose_params: dict = field(default_factory=dict)


def load_study(path):
    """The study file: study-level variables plus a ``specs`` mapping name -> Spec."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    specs = {}
    for name, s in (raw.pop("specs") or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError(f"spec name {name!r}: use letters, digits and _ only (it goes in the URL)")
        spec = Spec(name=name, **s)
        if spec.n_draw < 1 or not 0 <= spec.attention_pass <= spec.n_attention:
            raise ValueError(f"spec {name}: need n_draw >= 1 and 0 <= attention_pass <= n_attention")
        specs[name] = spec
    return raw, specs


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Assets:
    stimuli: dict          # stimulus_id (int) -> text
    graph: nx.Graph
    attention: list        # rows of attention_checks.csv, in file order
    sha256: dict           # file name -> sha256


def load_assets(assets_root, stimuli_name: str, graph_name: str, n_attention: int) -> Assets:
    folder = Path(assets_root) / stimuli_name
    stim_path, graph_path, att_path = folder / "stimuli.csv", folder / f"{graph_name}.json", folder / "attention_checks.csv"

    stimuli = {}
    with open(stim_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sid, text = int(row["stimulus_id"]), row["stimulus"].strip()
            if not text:
                raise ValueError(f"{stim_path}: empty stimulus for id {sid}")
            if sid in stimuli:
                raise ValueError(f"{stim_path}: duplicate stimulus_id {sid}")
            stimuli[sid] = text
    if len(set(stimuli.values())) != len(stimuli):
        raise ValueError(f"{stim_path}: duplicate stimulus texts")

    data = json.loads(graph_path.read_text(encoding="utf-8"))
    graph = nx.node_link_graph(data, edges="edges" if "edges" in data else "links")
    unknown = set(graph.nodes) - set(stimuli)
    if unknown:
        raise ValueError(f"{graph_path}: nodes with no stimulus in stimuli.csv: {sorted(unknown)}")
    if graph.number_of_nodes() == 0:
        raise ValueError(f"{graph_path}: empty graph")

    with open(att_path, newline="", encoding="utf-8") as f:
        attention = list(csv.DictReader(f))
    if len({a["att_id"] for a in attention}) != len(attention):
        raise ValueError(f"{att_path}: duplicate att_id")
    for a in attention:
        if a["answer"] not in ("A", "B") or not a["stimulus_A"].strip() or not a["stimulus_B"].strip():
            raise ValueError(f"{att_path}: bad row {a}")
    if n_attention > len(attention):
        raise ValueError(f"{att_path}: n_attention={n_attention} but only {len(attention)} items")

    return Assets(stimuli, graph, attention,
                  {p.name: sha256_file(p) for p in (stim_path, graph_path, att_path)})


def build_proposal(spec: Spec, assets: Assets):
    cls = load_proposal_class(spec.propose_fn)
    return cls(assets.graph, **spec.propose_params)


# ---------------------------------------------------------------- one session

class Session:
    """One chain. ``pending`` is the trial on screen; ``choose`` answers it and makes the next.

    Every random draw comes from ``rng`` in a fixed order, so the same seed and the same
    choices reproduce the same trials.
    """

    def __init__(self, spec: Spec, assets: Assets, proposal, seed: int):
        self.spec, self.assets, self.proposal, self.seed = spec, assets, proposal, seed
        self.rng = random.Random(seed)
        self.x = self.rng.choice(sorted(assets.graph.nodes))
        items = self.rng.sample(assets.attention, spec.n_attention)
        # schedule: ("chain", i_draw) or ("attention", item); attention placed after the given number of draws
        after = attention_after(spec.n_draw, spec.n_attention)
        self.schedule = []
        for d in range(spec.n_draw + 1):
            self.schedule += [("attention", items[k]) for k, a in enumerate(after) if a == d]
            if d < spec.n_draw:
                self.schedule.append(("chain", d))
        self.i_trial = 0
        self.pending = self._make_trial()

    @property
    def n_trials(self) -> int:
        return len(self.schedule)

    @property
    def done(self) -> bool:
        return self.pending is None

    def _make_trial(self):
        if self.i_trial >= len(self.schedule):
            return None
        kind, what = self.schedule[self.i_trial]
        if kind == "attention":
            return {"i_trial": self.i_trial, "trial_type": "attention", "att_id": what["att_id"],
                    "left_text": what["stimulus_A"], "right_text": what["stimulus_B"], "answer": what["answer"]}
        x_star = self.proposal.propose(self.x, self.rng)
        x_left = self.rng.random() < 0.5
        left, right = (self.x, x_star) if x_left else (x_star, self.x)
        return {"i_trial": self.i_trial, "trial_type": "chain", "i_draw": what, "x_id": self.x, "x_star_id": x_star,
                "left_id": left, "right_id": right,
                "left_text": self.assets.stimuli[left], "right_text": self.assets.stimuli[right]}

    def choose(self, side: str) -> dict:
        """Answer the pending trial with 'left' or 'right'; return its record and move on."""
        if self.done:
            raise ValueError("session already complete")
        if side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
        t = self.pending
        rec = {"i_trial": t["i_trial"], "trial_type": t["trial_type"], "chosen_side": side,
               "i_draw": None, "n_draw": self.spec.n_draw, "x_id": None, "x_star_id": None, "x_hat_id": None,
               "left_id": None, "att_id": None, "correct": None}
        if t["trial_type"] == "chain":
            x_hat = t["left_id"] if side == "left" else t["right_id"]
            rec.update(i_draw=t["i_draw"], x_id=t["x_id"], x_star_id=t["x_star_id"], x_hat_id=x_hat, left_id=t["left_id"])
            self.x = x_hat
        else:
            rec.update(att_id=t["att_id"], correct=(side == "left") == (t["answer"] == "A"))
        self.i_trial += 1
        self.pending = self._make_trial()
        return rec

    def peek(self) -> dict:
        """{side: the trial that choosing this side leads to (None = session ends)}, leaving the session unchanged.

        Lets the page show the next pair the moment the participant clicks. The choice itself
        never consumes randomness, so this is exactly what ``choose(side)`` will produce.
        """
        if self.done:
            return {}
        saved = (self.rng.getstate(), self.x, self.i_trial, self.pending)
        out = {}
        for side in SIDES:
            self.choose(side)
            out[side] = self.pending
            self.rng.setstate(saved[0])
            self.x, self.i_trial, self.pending = saved[1:]
        return out

    def replay(self, records: list):
        """Re-apply recorded choices (after a restart) and check they reproduce the recorded trials."""
        for r in records:
            t = self.pending
            if t is None or r["i_trial"] != t["i_trial"] or r["trial_type"] != t["trial_type"] or \
                    r.get("x_star_id") != t.get("x_star_id") or r.get("att_id") != t.get("att_id") or \
                    r.get("left_id") != t.get("left_id"):
                raise ValueError(f"replay mismatch at trial {r.get('i_trial')}: assets or code changed since the session started")
            self.choose(r["chosen_side"])
