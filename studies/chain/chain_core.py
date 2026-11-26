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
SKIP = "skip"   # the participant skipped the pair ("Feeling uncomfortable with these items?")

# <user_id>--spec=<spec>: one session per user and spec; user_id may contain single hyphens, never "--"
SID_RE = re.compile(r"(?P<user>[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)--spec=(?P<spec>[A-Za-z0-9_]+)")
SESSION_DIR_RE = re.compile(r"spec=[A-Za-z0-9_]+")   # {user_id}/spec={spec}/


def parse_sid(sid: str):
    """(user_id, spec_name) from a session id, or None if it is malformed."""
    m = SID_RE.fullmatch(sid or "")  # fullmatch: "$" would accept a trailing newline
    return (m["user"], m["spec"]) if m else None


def session_files(root: Path) -> list[Path]:
    """Every session.json under a study folder, in the current layout (older layouts are skipped)."""
    return sorted(p for p in Path(root).glob("*/*/session.json") if SESSION_DIR_RE.fullmatch(p.parent.name))


def session_seed(study_id: str, user_id: str, spec_name: str) -> int:
    key = "\x1f".join([study_id, user_id, spec_name]).encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def pick_attention(items: list, k: int, rng: random.Random, key="att_id") -> list:
    """k attention items for one session. With k <= len(items): rng.sample (all distinct).
    With more: rounds of rng.sample over all items (the last round only as long as needed), so
    every item is used at most ceil(k / len(items)) times; a round never starts with the item
    that ended the previous one, so the same check never comes twice in a row."""
    if k > 0 and not items:
        raise ValueError("attention checks requested but the attention file has no items")
    out = []
    while len(out) < k:
        rnd = rng.sample(items, min(len(items), k - len(out)))
        if out and len(items) > 1 and rnd[0][key] == out[-1][key]:
            if len(rnd) > 1:
                rnd[0], rnd[1] = rnd[1], rnd[0]
            else:
                rnd[0] = rng.choice([it for it in items if it[key] != out[-1][key]])
        out += rnd
    return out


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
    n_draw: int           # draws per chain
    n_chain: int          # chains per session, run one after the other
    question: str
    n_attention: int      # attention checks per chain (K = n_attention * n_chain per session)
    attention_pass: int   # correct checks needed per chain (attention_pass * n_chain per session)
    propose_params: dict = field(default_factory=dict)
    completion_code: str = ""      # shown on the last screen (Prolific); empty = none


def load_study(path):
    """The study file: study-level variables plus a ``specs`` mapping name -> Spec."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    specs = {}
    for name, s in (raw.pop("specs") or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError(f"spec name {name!r}: use letters, digits and _ only (it goes in the URL)")
        spec = Spec(name=name, **s)
        if spec.n_draw < 1 or spec.n_chain < 1 or not 0 <= spec.attention_pass <= spec.n_attention:
            raise ValueError(f"spec {name}: need n_draw >= 1, n_chain >= 1 and 0 <= attention_pass <= n_attention")
        specs[name] = spec
    return raw, specs


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Assets:
    stimuli: dict          # stimulus_id (int) -> text
    graph: nx.Graph
    attention: list        # rows of attention_checks_choice.csv, in file order
    sha256: dict           # file name -> sha256


def load_assets(assets_root, stimuli_name: str, graph_name: str) -> Assets:
    folder = Path(assets_root) / stimuli_name
    stim_path, graph_path, att_path = folder / "stimuli.csv", folder / f"{graph_name}.json", folder / "attention_checks_choice.csv"

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
    return Assets(stimuli, graph, attention,
                  {p.name: sha256_file(p) for p in (stim_path, graph_path, att_path)})


def check_spec_assets(spec: Spec, assets: Assets):
    """Raise if this spec cannot run on these assets (checked at boot)."""
    if spec.n_chain > assets.graph.number_of_nodes():
        raise ValueError(f"spec {spec.name}: n_chain={spec.n_chain} chains need distinct starting stimuli, "
                         f"but the graph has only {assets.graph.number_of_nodes()} nodes")
    if spec.n_attention * spec.n_chain and not assets.attention:
        raise ValueError(f"spec {spec.name}: attention checks requested but the attention file is empty")


def attention_shortfall(spec: Spec, assets: Assets) -> str | None:
    """A boot warning when the session needs more checks than the file has (items get reused)."""
    k, m = spec.n_attention * spec.n_chain, len(assets.attention)
    if k > m:
        return (f"spec {spec.name}: n_attention * n_chain = {k} checks but only {m} items; "
                f"items are reused, each at most {-(-k // m)} times")
    return None


def build_proposal(spec: Spec, assets: Assets):
    cls = load_proposal_class(spec.propose_fn)
    return cls(assets.graph, **spec.propose_params)


# ---------------------------------------------------------------- one session

class Session:
    """One session: n_chain chains of n_draw draws each, one after the other, with
    K = n_attention * n_chain attention checks spread evenly over all n_chain * n_draw draws.
    Each chain starts at its own stimulus (drawn without replacement) with n_rejected = 0;
    the participant sees no break between chains.
    ``pending`` is the trial on screen; ``choose`` answers it and makes the next.
    ``skip`` replaces a chain trial without answering it: the chain stays at x and gets a new
    proposal (at a chain's first draw, the chain also gets a new starting stimulus); a skip is
    not a draw. Skipping an attention trial answers it (as incorrect).

    Every random draw comes from ``rng`` in a fixed order, so the same seed and the same
    choices reproduce the same trials.
    """

    def __init__(self, spec: Spec, assets: Assets, proposal, seed: int):
        self.spec, self.assets, self.proposal, self.seed = spec, assets, proposal, seed
        self.rng = random.Random(seed)
        check_spec_assets(spec, assets)
        self.starts = self.rng.sample(sorted(assets.graph.nodes), spec.n_chain)   # x0 of each chain, all distinct
        n_total, n_att = spec.n_draw * spec.n_chain, spec.n_attention * spec.n_chain
        items = pick_attention(assets.attention, n_att, self.rng)
        # schedule: ("chain", global draw index) or ("attention", item); attention placed after the given number of draws
        after = attention_after(n_total, n_att)
        self.schedule = []
        for d in range(n_total + 1):
            self.schedule += [("attention", items[k]) for k, a in enumerate(after) if a == d]
            if d < n_total:
                self.schedule.append(("chain", d))
        self.i_trial = 0
        self.seq = -1         # counts every trial put on screen (skips included): tells a stale answer from a live one
        self.retired = set()  # starting stimuli skipped at a chain's first draw, never used as a start again
        self.x = None
        self.n_rejected = 0   # chain proposals rejected in a row (x kept); attention trials do not count
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
        self.seq += 1
        kind, what = self.schedule[self.i_trial]
        if kind == "attention":
            return {"i_trial": self.i_trial, "seq": self.seq, "trial_type": "attention", "att_id": what["att_id"],
                    "left_text": what["stimulus_A"], "right_text": what["stimulus_B"], "answer": what["answer"]}
        chain_idx, i_draw = divmod(what, self.spec.n_draw)
        if i_draw == 0:       # a new chain: fresh start, fresh rejection count
            self.x, self.n_rejected = self.starts[chain_idx], 0
        x_star, info = self.proposal.propose(self.x, self.rng, self.n_rejected)
        if x_star == self.x:
            raise RuntimeError(f"{self.spec.propose_fn} proposed x itself")
        x_left = self.rng.random() < 0.5
        left, right = (self.x, x_star) if x_left else (x_star, self.x)
        return {"i_trial": self.i_trial, "seq": self.seq, "trial_type": "chain", "chain_idx": chain_idx, "i_draw": i_draw,
                "x_id": self.x, "x_star_id": x_star,
                "left_id": left, "right_id": right, "n_rejected": self.n_rejected,
                "proposal_kind": info.get("proposal_kind"), "n_steps": info.get("n_steps"),
                "left_text": self.assets.stimuli[left], "right_text": self.assets.stimuli[right]}

    def choose(self, side: str) -> dict:
        """Answer the pending trial with 'left' or 'right' ('skip' too on an attention trial);
        return its record and move on."""
        if self.done:
            raise ValueError("session already complete")
        t = self.pending
        if side not in SIDES and not (side == SKIP and t["trial_type"] == "attention"):
            raise ValueError(f"side must be one of {SIDES}")
        rec = {"i_trial": t["i_trial"], "trial_type": t["trial_type"], "chosen_side": side,
               "chain_idx": None, "i_draw": None, "n_draw": self.spec.n_draw, "n_chain": self.spec.n_chain, "x_id": None, "x_star_id": None, "x_hat_id": None,
               "left_id": None, "att_id": None, "correct": None,
               "proposal_kind": None, "n_steps": None, "n_rejected": None}
        if t["trial_type"] == "chain":
            x_hat = t["left_id"] if side == "left" else t["right_id"]
            rec.update(chain_idx=t["chain_idx"], i_draw=t["i_draw"], x_id=t["x_id"], x_star_id=t["x_star_id"], x_hat_id=x_hat, left_id=t["left_id"],
                       proposal_kind=t["proposal_kind"], n_steps=t["n_steps"], n_rejected=t["n_rejected"])
            self.n_rejected = self.n_rejected + 1 if x_hat == self.x else 0
            self.x = x_hat
        else:
            rec.update(att_id=t["att_id"], correct=side != SKIP and (side == "left") == (t["answer"] == "A"))
        self.i_trial += 1
        self.pending = self._make_trial()
        return rec

    def skip(self) -> dict:
        """Skip the pending trial. Chain trial: returns the skip event {i_trial, chain_idx, i_draw,
        x_id, x_star_id} and puts a new trial for the same draw on screen. Attention trial:
        answered as incorrect, returns its record (with trial_type)."""
        if self.done:
            raise ValueError("session already complete")
        t = self.pending
        if t["trial_type"] == "attention":
            return self.choose(SKIP)
        event = {k: t[k] for k in ("i_trial", "chain_idx", "i_draw", "x_id", "x_star_id")}
        if t["i_draw"] == 0:
            # skipped at the chain's first draw: the start may be what felt uncomfortable, so the
            # chain gets a new start (not a start of any chain, not skipped before, not this x*)
            self.retired.add(t["x_id"])
            taken = self.retired | set(self.starts) | {t["x_star_id"]}
            free = [n for n in sorted(self.assets.graph.nodes) if n not in taken]
            if free:
                self.starts[t["chain_idx"]] = self.rng.choice(free)
        self.pending = self._make_trial()   # same i_trial and draw; _make_trial re-reads the start at i_draw 0
        return event

    def _state(self):
        return (self.rng.getstate(), self.x, self.i_trial, self.pending, self.n_rejected,
                self.seq, list(self.starts), set(self.retired))

    def _restore(self, s):
        self.rng.setstate(s[0])
        self.x, self.i_trial, self.pending, self.n_rejected, self.seq = s[1:6]
        self.starts, self.retired = list(s[6]), set(s[7])

    def peek(self) -> dict:
        """{'left'|'right'|'skip': the trial that this answer leads to (None = session ends)}, leaving
        the session unchanged. Lets the page show the next pair the moment the participant clicks.
        The choice itself never consumes randomness, so this is exactly what choose/skip will produce."""
        if self.done:
            return {}
        saved = self._state()
        out = {}
        for side in SIDES + (SKIP,):
            self.skip() if side == SKIP else self.choose(side)
            out[side] = self.pending
            self._restore(saved)
        return out

    def replay(self, records: list, skips: list = ()):
        """Re-apply recorded choices and skips (after a restart) and check they reproduce the recorded trials.
        A skip happens before the record with the same i_trial (skips do not advance i_trial)."""
        skips = list(skips)
        def apply_skips():
            while skips and self.pending is not None and skips[0]["i_trial"] == self.pending["i_trial"]:
                ev = skips.pop(0)
                if self.pending.get("x_star_id") != ev["x_star_id"]:
                    raise ValueError(f"replay mismatch at skip of trial {ev['i_trial']}: assets or code changed")
                self.skip()
        for r in records:
            apply_skips()
            t = self.pending
            if t is None or r["i_trial"] != t["i_trial"] or r["trial_type"] != t["trial_type"] or \
                    r.get("x_star_id") != t.get("x_star_id") or r.get("att_id") != t.get("att_id") or \
                    r.get("left_id") != t.get("left_id"):
                raise ValueError(f"replay mismatch at trial {r.get('i_trial')}: assets or code changed since the session started")
            self.choose(r["chosen_side"])
        apply_skips()
        if skips:
            raise ValueError("replay mismatch: skips left over after the last record")
