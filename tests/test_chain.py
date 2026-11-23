"""CPU-only tests for the proposal registry, the chain logic and the HTTP endpoints.

    python -m unittest discover -s tests -v
"""

import json
import random
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import networkx as nx
from flask import Flask

from chain_core import (Session, Spec, attention_after, load_assets, parse_sid,
                        session_seed)
from chain_server import ChainStudy, make_blueprint
from proposal import load_proposal_class
from proposal.graph.uniform_neighbor import UniformNeighbor

ASSETS = ROOT / "assets" / "stimuli"
STIM = "dummy_emosent_binary"


def spec(**kw):
    base = dict(name="t", stimuli_name=STIM, graph_name="b=4--emb=spades", propose_fn="graph/uniform_neighbor",
                n_draw=20, question="q?", n_attention=3, attention_pass=3)
    return Spec(**{**base, **kw})


def new_session(seed=1, **kw):
    sp = spec(**kw)
    assets = load_assets(ASSETS, sp.stimuli_name, sp.graph_name, sp.n_attention)
    return Session(sp, assets, UniformNeighbor(assets.graph), seed)


class TestProposal(unittest.TestCase):
    def test_registry_finds_the_subclass(self):
        self.assertIs(load_proposal_class("graph/uniform_neighbor"), UniformNeighbor)

    def test_registry_rejects_bad_names(self):
        for bad in ["uniform_neighbor", "graph/base", "graph/nope", "../x/y", "graph/a.b"]:
            with self.assertRaises(ValueError, msg=bad):
                load_proposal_class(bad)

    def test_proposes_a_neighbour_deterministically(self):
        g = nx.random_regular_graph(4, 20, seed=0)
        p = UniformNeighbor(g)
        a = [p.propose(x, random.Random(7)) for x in g.nodes]
        b = [p.propose(x, random.Random(7)) for x in g.nodes]
        self.assertEqual(a, b)
        self.assertTrue(all(g.has_edge(x, y) for x, y in zip(g.nodes, a)))

    def test_uniform_over_neighbours(self):
        g = nx.cycle_graph(6)
        rng = random.Random(0)
        counts = Counter(UniformNeighbor(g).propose(0, rng) for _ in range(4000))
        self.assertEqual(set(counts), {1, 5})
        self.assertLess(abs(counts[1] - 2000), 200)

    def test_checks(self):
        self.assertEqual(UniformNeighbor(nx.cycle_graph(5)).check(), [])
        self.assertTrue(UniformNeighbor(nx.path_graph(4)).check())  # not regular -> warning
        g = nx.Graph()
        g.add_nodes_from([1, 2])
        with self.assertRaises(ValueError):
            UniformNeighbor(g)  # isolated nodes


class TestCore(unittest.TestCase):
    def test_parse_sid(self):
        self.assertEqual(parse_sid("u-1_a--spec=dummy_envy--chain=3"), ("u-1_a", "dummy_envy", 3))
        for bad in ["", "u1", "u1--spec=a", "u--x--spec=a--chain=1", "../u--spec=a--chain=1",
                    "u1--spec=a-b--chain=1", "u1--spec=a--chain=x", "u1--spec=a--chain=1\n"]:
            self.assertIsNone(parse_sid(bad), bad)

    def test_seed_differs_per_chain(self):
        self.assertNotEqual(session_seed("s", "u", "a", 1), session_seed("s", "u", "a", 2))
        self.assertEqual(session_seed("s", "u", "a", 1), session_seed("s", "u", "a", 1))

    def test_attention_spread(self):
        self.assertEqual(attention_after(20, 3), [5, 10, 15])
        self.assertEqual(attention_after(20, 0), [])

    def test_assets_match_graph(self):
        a = load_assets(ASSETS, STIM, "exhaustive_graph", 3)
        self.assertEqual(set(a.graph.nodes), set(a.stimuli))
        self.assertEqual(len(a.attention), 10)

    def test_full_session_invariants(self):
        s = new_session(seed=11)
        self.assertEqual(s.n_trials, 23)
        recs, rng = [], random.Random(3)
        while not s.done:
            recs.append(s.choose(rng.choice(["left", "right"])))
        chain = [r for r in recs if r["trial_type"] == "chain"]
        att = [r for r in recs if r["trial_type"] == "attention"]
        self.assertEqual([r["i_draw"] for r in chain], list(range(20)))
        self.assertEqual(len(att), 3)
        self.assertEqual(len({r["att_id"] for r in att}), 3)                      # without replacement
        self.assertEqual([recs.index(r) for r in att], [5, 11, 17])              # after draws 5, 10, 15
        for prev, r in zip(chain, chain[1:]):
            self.assertEqual(r["x_id"], prev["x_hat_id"])                        # x <- x_hat
        for r in chain:
            self.assertIn(r["x_hat_id"], (r["x_id"], r["x_star_id"]))
            self.assertTrue(s.assets.graph.has_edge(r["x_id"], r["x_star_id"]))

    def test_order_is_randomised(self):
        s = new_session(seed=5, n_draw=200, n_attention=0, attention_pass=0)
        left_is_x = 0
        while not s.done:
            left_is_x += s.pending["left_id"] == s.pending["x_id"]
            s.choose("left")
        self.assertTrue(70 < left_is_x < 130, left_is_x)

    def test_attention_scoring(self):
        s = new_session(seed=2, n_draw=2, n_attention=1, attention_pass=1)
        while s.pending["trial_type"] != "attention":
            s.choose("left")
        right = "left" if s.pending["answer"] == "A" else "right"
        self.assertTrue(s.choose(right)["correct"])

    def test_replay_reproduces_and_detects_change(self):
        a = new_session(seed=9)
        recs = [a.choose(side) for side in ["left", "right", "right", "left", "left", "right", "left"]]
        b = new_session(seed=9)
        b.replay(recs)
        self.assertEqual(b.pending, a.pending)
        c = new_session(seed=10)
        with self.assertRaises(ValueError):
            c.replay(recs)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        study = {"study_id": "test", "output_root": self.tmp.name, "assets_root": str(ASSETS)}
        self.specs = {"s": spec(name="s", n_draw=4, n_attention=1, attention_pass=1)}
        self.chain = ChainStudy(study, self.specs)
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(make_blueprint(self.chain, lambda key: key == "k"))
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, sid):
        with self.client.session_transaction() as sess:
            sess["username"] = sid

    def run_session(self, sid="u1--spec=s--chain=1"):
        self.login(sid)
        t = self.client.get("/chain/next").get_json()
        n = 0
        while not t["done"]:
            t = self.client.post("/chain/choose", json={"i_trial": t["i_trial"], "side": "left", "rt_ms": 1234}).get_json()
            n += 1
        return n

    def test_needs_login_and_valid_sid(self):
        self.assertEqual(self.client.get("/chain/next").status_code, 401)
        self.login("not a sid")
        self.assertEqual(self.client.get("/chain/next").status_code, 400)
        self.login("u1--spec=unknown--chain=1")
        self.assertEqual(self.client.get("/chain/next").status_code, 404)

    def test_full_session_writes_files(self):
        self.assertEqual(self.run_session(), 5)
        folder = Path(self.tmp.name) / "test" / "u1" / "spec=s--chain=1"
        lines = (folder / "trials.jsonl").read_text().splitlines()
        meta = json.loads((folder / "session.json").read_text())
        self.assertEqual(len(lines), 5)
        self.assertEqual(meta["status"], "complete")
        self.assertEqual(meta["n_draw_done"], 4)
        self.assertEqual(meta["median_rt_ms"], 1234)
        self.assertIsNotNone(meta["attention_passed"])
        rec = json.loads(lines[0])
        self.assertEqual(rec["user_id"], "u1")
        self.assertIsNotNone(rec["server_ms"])

    def test_bad_input_and_stale_answer(self):
        self.login("u1--spec=s--chain=1")
        t = self.client.get("/chain/next").get_json()
        for body in [{"i_trial": 0, "side": "up"}, {"i_trial": "0", "side": "left"},
                     {"i_trial": 0, "side": "left", "rt_ms": -5}, {"i_trial": 0, "side": "left", "rt_ms": "fast"}]:
            self.assertEqual(self.client.post("/chain/choose", json=body).status_code, 400, body)
        ok = self.client.post("/chain/choose", json={"i_trial": t["i_trial"], "side": "left", "rt_ms": 10})
        self.assertEqual(ok.status_code, 200)
        again = self.client.post("/chain/choose", json={"i_trial": t["i_trial"], "side": "left", "rt_ms": 10})
        self.assertEqual(again.status_code, 409)            # double click is not recorded twice
        self.assertEqual(again.get_json()["i_trial"], t["i_trial"] + 1)

    def test_resume_after_restart(self):
        self.login("u2--spec=s--chain=1")
        t = self.client.get("/chain/next").get_json()
        t = self.client.post("/chain/choose", json={"i_trial": t["i_trial"], "side": "right", "rt_ms": 5}).get_json()
        before = self.chain.sessions["u2--spec=s--chain=1"][0].pending
        self.chain.sessions.clear()                          # what a restart loses
        after = self.client.get("/chain/next").get_json()
        self.assertEqual(after["i_trial"], t["i_trial"])
        self.assertEqual((after["left"], after["right"]), (before["left_text"], before["right_text"]))

    def test_chains_are_separate(self):
        self.run_session("u1--spec=s--chain=1")
        self.run_session("u1--spec=s--chain=2")
        self.assertEqual(len(list((Path(self.tmp.name) / "test" / "u1").iterdir())), 2)

    def test_admin(self):
        self.run_session()
        self.assertEqual(self.client.get("/chain/admin").status_code, 403)
        page = self.client.get("/chain/admin?key=k")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"u1--spec=s--chain=1", page.data)
        csv_text = self.client.get("/chain/admin.csv?key=k").data.decode()
        self.assertEqual(len(csv_text.strip().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
