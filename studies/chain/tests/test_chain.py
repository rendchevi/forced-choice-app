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
from flask import Flask, session

from chain_core import (Session, Spec, attention_after, load_assets, parse_sid, pick_attention,
                        session_seed)
from chain_server import ChainStudy, make_blueprint
from prolific import prolific_ids
from proposal import load_proposal_class
from proposal.graph.dmcmcp import DMCMCP

ASSETS = ROOT.parents[1] / "assets" / "stimuli"   # repo-level shared assets
STIM = "dummy_emosent_binary"


def spec(**kw):
    base = dict(name="t", stimuli_name=STIM, graph_name="b=4--emb=spades", propose_fn="graph/dmcmcp",
                n_draw=20, n_chain=1, question="q?", n_attention=3, attention_pass=3)
    return Spec(**{**base, **kw})


def new_session(seed=1, **kw):
    sp = spec(**kw)
    assets = load_assets(ASSETS, sp.stimuli_name, sp.graph_name)
    return Session(sp, assets, DMCMCP(assets.graph, **sp.propose_params), seed)


def freq(p, x, n=20000, n_rejected=0, seed=0):
    rng = random.Random(seed)
    return Counter(p.propose(x, rng, n_rejected)[0] for _ in range(n))


class TestProposal(unittest.TestCase):
    def test_registry_finds_the_subclass(self):
        self.assertIs(load_proposal_class("graph/dmcmcp"), DMCMCP)

    def test_registry_rejects_bad_names(self):
        for bad in ["dmcmcp", "graph/base", "graph/nope", "../x/y", "graph/a.b"]:
            with self.assertRaises(ValueError, msg=bad):
                load_proposal_class(bad)

    def test_deterministic_and_never_x(self):
        g = nx.random_regular_graph(4, 20, seed=0)
        p = DMCMCP(g, p_geom=0.6, p_jump=0.2)
        a = [p.propose(x, random.Random(7), 0) for x in g.nodes]
        self.assertEqual(a, [p.propose(x, random.Random(7), 0) for x in g.nodes])
        rng = random.Random(1)
        self.assertTrue(all(p.propose(x, rng, 0)[0] != x for x in g.nodes for _ in range(200)))

    def test_p_geom_0_is_uniform_neighbour(self):
        p = DMCMCP(nx.cycle_graph(6))
        c = freq(p, 0, n=4000)
        self.assertEqual(set(c), {1, 5})
        self.assertLess(abs(c[1] - 2000), 200)
        self.assertEqual(p.propose(0, random.Random(0), 0)[1], {"proposal_kind": "walk", "n_steps": 1})

    def test_walk_matches_exact_q(self):
        # Q = (1 - p) P (I - p P)^-1, then the walk is redrawn when it ends at x
        import numpy as np
        g, pg, n = nx.random_regular_graph(3, 10, seed=2), 0.5, 40000
        nodes = sorted(g)
        P = nx.to_numpy_array(g, nodelist=nodes)
        P /= P.sum(1, keepdims=True)
        Q = (1 - pg) * P @ np.linalg.inv(np.eye(len(nodes)) - pg * P)
        q = Q[0] / (1 - Q[0, 0])
        q[0] = 0
        c = freq(DMCMCP(g, p_geom=pg), nodes[0], n=n)
        emp = np.array([c[v] / n for v in nodes])
        self.assertLess(np.abs(emp - q).max(), 4 * np.sqrt(q.max() / n))
        rng = random.Random(3)
        steps = [DMCMCP(g, p_geom=pg).propose(0, rng, 0)[1]["n_steps"] for _ in range(20000)]
        self.assertGreater(np.mean(steps), 1.8)   # 1/(1-p) = 2 before the redraw (redraws favour longer walks slightly)

    def test_jump_frequency_and_support(self):
        g = nx.cycle_graph(12)
        rng = random.Random(4)
        kinds = Counter(DMCMCP(g, p_jump=0.3).propose(0, rng, 0)[1]["proposal_kind"] for _ in range(20000))
        self.assertLess(abs(kinds["jump"] / 20000 - 0.3), 0.02)
        c = freq(DMCMCP(g, p_jump=1.0), 0, n=22000)
        self.assertEqual(set(c), set(range(1, 12)))           # X \ {x}, every other node
        self.assertLess(max(c.values()) - min(c.values()), 400)

    def test_stuck_after_every_n_stuck_rejections(self):
        p = DMCMCP(nx.cycle_graph(12), n_stuck=3)
        kind = lambda k: p.propose(0, random.Random(k), k)[1]["proposal_kind"]
        self.assertEqual([kind(k) for k in range(8)], ["walk", "walk", "walk", "stuck", "walk", "walk", "stuck", "walk"])
        self.assertEqual(DMCMCP(nx.cycle_graph(12)).propose(0, random.Random(0), 99)[1]["proposal_kind"], "walk")

    def test_bad_params_and_checks(self):
        for kw in [{"p_geom": 1.0}, {"p_geom": -0.1}, {"p_jump": 1.5}, {"n_stuck": 0}, {"n_stuck": 2.5}]:
            with self.assertRaises(ValueError, msg=kw):
                DMCMCP(nx.cycle_graph(5), **kw)
        g = nx.Graph()
        g.add_nodes_from([1, 2])
        with self.assertRaises(ValueError):
            DMCMCP(g)                                           # isolated nodes
        self.assertEqual(DMCMCP(nx.cycle_graph(5)).check(), [])
        self.assertEqual(len(DMCMCP(nx.path_graph(4), n_stuck=2).check()), 2)  # not regular + history-dependent


class TestRejectionCount(unittest.TestCase):
    def test_session_counts_rejections_and_triggers_stuck(self):
        s = new_session(seed=5, propose_params={"p_geom": 0.0, "p_jump": 0.0, "n_stuck": 2}, n_attention=0, attention_pass=0)
        kinds = []
        while not s.done:
            t = s.pending
            kinds.append((t["n_rejected"], t["proposal_kind"]))
            s.choose("left" if t["left_id"] == t["x_id"] else "right")   # always keep x
        self.assertEqual(kinds[:5], [(0, "walk"), (1, "walk"), (2, "stuck"), (3, "walk"), (4, "stuck")])

    def test_acceptance_resets_and_peek_restores(self):
        s = new_session(seed=6, propose_params={"p_geom": 0.3, "p_jump": 0.1, "n_stuck": 3})
        while not s.done:
            t = s.pending
            ahead = s.peek()
            side = "right" if t["i_trial"] % 3 else "left"
            rec = s.choose(side)
            self.assertEqual(s.pending, ahead[side])
            if t["trial_type"] == "chain":
                self.assertEqual((rec["proposal_kind"], rec["n_rejected"]), (t["proposal_kind"], t["n_rejected"]))
                if s.pending and s.pending["trial_type"] == "chain":
                    want = t["n_rejected"] + 1 if rec["x_hat_id"] == rec["x_id"] else 0
                    self.assertEqual(s.pending["n_rejected"], want)


class TestCore(unittest.TestCase):
    def test_parse_sid(self):
        self.assertEqual(parse_sid("u-1_a--spec=dummy_envy"), ("u-1_a", "dummy_envy"))
        for bad in ["", "u1", "u1--spec=", "u--x--spec=a", "../u--spec=a", "u1--spec=a-b",
                    "u1--spec=a--chain=1", "u1--spec=a\n"]:
            self.assertIsNone(parse_sid(bad), bad)

    def test_seed_differs_per_user_and_spec(self):
        self.assertNotEqual(session_seed("s", "u", "a"), session_seed("s", "u", "b"))
        self.assertNotEqual(session_seed("s", "u", "a"), session_seed("s", "v", "a"))
        self.assertEqual(session_seed("s", "u", "a"), session_seed("s", "u", "a"))

    def test_attention_spread(self):
        self.assertEqual(attention_after(20, 3), [5, 10, 15])
        self.assertEqual(attention_after(20, 0), [])

    def test_assets_match_graph(self):
        a = load_assets(ASSETS, STIM, "exhaustive_graph")
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

    def test_multi_chain_session(self):
        s = new_session(seed=12, n_draw=6, n_chain=3, n_attention=2, attention_pass=2)
        self.assertEqual(s.n_trials, 3 * 6 + 3 * 2)
        recs, rng = [], random.Random(4)
        while not s.done:
            recs.append(s.choose(rng.choice(["left", "right"])))
        chain = [r for r in recs if r["trial_type"] == "chain"]
        att = [r for r in recs if r["trial_type"] == "attention"]
        self.assertEqual([(r["chain_idx"], r["i_draw"]) for r in chain], [(c, d) for c in range(3) for d in range(6)])
        self.assertEqual(len({r["att_id"] for r in att}), 6)                     # K = n_attention * n_chain, distinct
        # check k after global draw floor(k * 18 / 7): 2, 5, 7, 10, 12, 15
        draws_before = [sum(r["trial_type"] == "chain" for r in recs[:recs.index(a)]) for a in att]
        self.assertEqual(draws_before, [2, 5, 7, 10, 12, 15])
        starts = [r["x_id"] for r in chain if r["i_draw"] == 0]
        self.assertEqual(starts, s.starts)
        self.assertEqual(len(set(starts)), 3)                                    # distinct starting stimuli
        for prev, r in zip(chain, chain[1:]):
            if r["i_draw"] > 0:
                self.assertEqual(r["x_id"], prev["x_hat_id"])                    # within a chain: x <- x_hat
            else:
                self.assertEqual(r["n_rejected"], 0)                             # new chain: count restarts

    def test_n_chain_must_fit_the_assets(self):
        with self.assertRaises(ValueError):
            new_session(n_chain=24)                                              # 23 stimuli

    def test_too_few_attention_items_are_reused_evenly(self):
        s = new_session(n_draw=5, n_chain=4, n_attention=3)                      # 12 checks, 10 items
        ids = [what["att_id"] for kind, what in s.schedule if kind == "attention"]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 10)                                      # every item used
        self.assertLessEqual(max(Counter(ids).values()), 2)                      # at most ceil(12/10)
        self.assertTrue(all(a != b for a, b in zip(ids, ids[1:])))               # never twice in a row

    def test_pick_attention(self):
        items = [{"att_id": i} for i in range(3)]
        for k in range(0, 20):
            for seed in range(30):
                got = [it["att_id"] for it in pick_attention(items, k, random.Random(seed))]
                self.assertEqual(len(got), k)
                if k:
                    self.assertLessEqual(max(Counter(got).values()), -(-k // 3))
                self.assertTrue(all(a != b for a, b in zip(got, got[1:])), (k, seed, got))
        rng_a, rng_b = random.Random(1), random.Random(1)
        self.assertEqual(pick_attention(items, 2, rng_a), rng_b.sample(items, 2))   # k <= M: plain sample
        with self.assertRaises(ValueError):
            pick_attention([], 1, random.Random(0))

    def test_skip_mid_chain_keeps_x_and_is_not_a_draw(self):
        s = new_session(seed=21, n_draw=5, n_chain=1, n_attention=0, attention_pass=0)
        s.choose("left")
        t = s.pending
        ev = s.skip()
        self.assertEqual((ev["i_trial"], ev["i_draw"], ev["x_id"], ev["x_star_id"]), (t["i_trial"], t["i_draw"], t["x_id"], t["x_star_id"]))
        u = s.pending
        self.assertEqual((u["i_trial"], u["i_draw"], u["x_id"], u["n_rejected"]), (t["i_trial"], t["i_draw"], t["x_id"], t["n_rejected"]))
        self.assertEqual(u["seq"], t["seq"] + 1)
        n = 0
        while not s.done:
            s.choose("right")
            n += 1
        self.assertEqual(n, 4)                                                   # still n_draw - 1 more draws

    def test_skip_at_first_draw_gives_a_new_start(self):
        s = new_session(seed=22, n_draw=3, n_chain=2, n_attention=0, attention_pass=0)
        t = s.pending
        s.skip()
        u = s.pending
        self.assertEqual((u["i_draw"], u["chain_idx"]), (0, 0))
        self.assertNotIn(u["x_id"], (t["x_id"], t["x_star_id"], s.starts[1]))   # new start, distinct from the other chain's
        s.skip()
        self.assertNotIn(s.pending["x_id"], (t["x_id"], u["x_id"]))             # skipped starts never come back

    def test_skip_on_attention_counts_as_wrong(self):
        s = new_session(seed=2, n_draw=2, n_attention=1, attention_pass=1)
        while s.pending["trial_type"] != "attention":
            s.choose("left")
        rec = s.skip()
        self.assertEqual((rec["trial_type"], rec["chosen_side"], rec["correct"]), ("attention", "skip", False))
        with self.assertRaises(ValueError):
            new_session().choose("skip")                                         # chain trials skip via skip()

    def test_peek_skip_branch_and_replay_with_skips(self):
        a = new_session(seed=23, n_draw=6, n_chain=2, n_attention=1, attention_pass=1)
        rng, recs, skips = random.Random(5), [], []
        while not a.done:
            ahead = a.peek()
            if a.pending["trial_type"] == "chain" and rng.random() < 0.3:
                skips.append(a.skip())
                self.assertEqual(a.pending, ahead["skip"])
            else:
                side = rng.choice(["left", "right"])
                recs.append(a.choose(side))
                self.assertEqual(a.pending, ahead[side])
        self.assertTrue(skips)
        b = new_session(seed=23, n_draw=6, n_chain=2, n_attention=1, attention_pass=1)
        b.replay(recs, skips)
        self.assertTrue(b.done)
        c = new_session(seed=23, n_draw=6, n_chain=2, n_attention=1, attention_pass=1)
        with self.assertRaises(ValueError):
            c.replay(recs)                                                       # skips are part of the history

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

    def test_peek_matches_choose(self):
        for side_seq_seed in range(5):
            s, rng = new_session(seed=side_seq_seed), random.Random(side_seq_seed)
            while not s.done:
                side = rng.choice(["left", "right"])
                peeked = s.peek()
                state = (s.rng.getstate(), s.x, s.i_trial, s.pending)
                self.assertEqual(s.peek(), peeked)                       # peek leaves the session unchanged
                self.assertEqual((s.rng.getstate(), s.x, s.i_trial, s.pending), state)
                s.choose(side)
                self.assertEqual(s.pending, peeked[side])

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
        study = {"study_id": "test", "output_root": self.tmp.name, "assets_root": str(ASSETS),
                 "abandon_after_min": 30}
        self.specs = {"s": spec(name="s", n_draw=4, n_chain=2, n_attention=1, attention_pass=1, completion_code="CODE1")}
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

    def run_session(self, sid="u1--spec=s"):
        self.login(sid)
        t = self.client.get("/chain/next").get_json()
        n = 0
        while not t["done"]:
            t = self.client.post("/chain/choose", json={"seq": t["trial"]["seq"], "side": "left", "rt_ms": 1234}).get_json()
            n += 1
        return n

    def test_needs_login_and_valid_sid(self):
        self.assertEqual(self.client.get("/chain/next").status_code, 401)
        self.login("not a sid")
        self.assertEqual(self.client.get("/chain/next").status_code, 400)
        self.login("u1--spec=unknown")
        self.assertEqual(self.client.get("/chain/next").status_code, 404)

    def test_full_session_writes_files(self):
        self.assertEqual(self.run_session(), 10)                               # 2 chains x 4 draws + 2 checks
        folder = Path(self.tmp.name) / "test" / "u1" / "spec=s"
        lines = (folder / "trials.jsonl").read_text().splitlines()
        meta = json.loads((folder / "session.json").read_text())
        self.assertEqual(len(lines), 10)
        self.assertEqual(meta["status"], "complete")
        self.assertEqual(meta["n_draw_done"], 8)
        self.assertEqual(meta["median_rt_ms"], 1234)
        self.assertIsNotNone(meta["attention_passed"])
        rec = json.loads(lines[0])
        self.assertEqual(rec["user_id"], "u1")
        self.assertIsNotNone(rec["server_ms"])

    def test_prefetch_is_what_the_server_then_serves(self):
        self.login("u3--spec=s")
        t = self.client.get("/chain/next").get_json()
        while not t["done"]:
            side = "right" if t["trial"]["i_trial"] % 2 else "left"
            promised = t["next"][side]
            t = self.client.post("/chain/choose", json={"seq": t["trial"]["seq"], "side": side, "rt_ms": 1}).get_json()
            if t["done"]:
                self.assertIsNone(promised)
            else:
                self.assertEqual(t["trial"], promised)

    def test_completion_code_only_when_done(self):
        self.login("u4--spec=s")
        t = self.client.get("/chain/next").get_json()
        self.assertNotIn("completion_code", t)
        self.run_session("u4--spec=s")
        self.assertEqual(self.client.get("/chain/next").get_json(),
                         {"done": True, "completion_code": "CODE1", "redirect_url": None})  # test link: no Prolific

    def test_bad_input_and_stale_answer(self):
        self.login("u1--spec=s")
        t = self.client.get("/chain/next").get_json()
        for body in [{"seq": 0, "side": "up"}, {"seq": "0", "side": "left"}, {"i_trial": 0, "side": "left"},
                     {"seq": 0, "side": "left", "rt_ms": -5}, {"seq": 0, "side": "left", "rt_ms": "fast"}]:
            self.assertEqual(self.client.post("/chain/choose", json=body).status_code, 400, body)
        ok = self.client.post("/chain/choose", json={"seq": t["trial"]["seq"], "side": "left", "rt_ms": 10})
        self.assertEqual(ok.status_code, 200)
        again = self.client.post("/chain/choose", json={"seq": t["trial"]["seq"], "side": "left", "rt_ms": 10})
        self.assertEqual(again.status_code, 409)            # double click is not recorded twice
        self.assertEqual(again.get_json()["trial"]["i_trial"], t["trial"]["i_trial"] + 1)

    def test_skip_over_http(self):
        self.login("u5--spec=s")
        t = self.client.get("/chain/next").get_json()
        promised = t["next"]["skip"]
        r = self.client.post("/chain/choose", json={"seq": t["trial"]["seq"], "side": "skip", "rt_ms": 30})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["trial"], promised)
        again = self.client.post("/chain/choose", json={"seq": t["trial"]["seq"], "side": "skip", "rt_ms": 30})
        self.assertEqual(again.status_code, 409)                                 # double click: one skip only
        folder = Path(self.tmp.name) / "test" / "u5" / "spec=s"
        meta = json.loads((folder / "session.json").read_text())
        self.assertEqual(len(meta["skips"]), 1)
        self.assertEqual((folder / "trials.jsonl").read_text(), "")              # not a trial
        self.chain.sessions.clear()                                              # restart: the skip is replayed
        self.assertEqual(self.client.get("/chain/next").get_json()["trial"], promised)
        self.assertEqual(self.chain.summaries()[0]["skips"], 1)

    def test_resume_after_restart(self):
        self.login("u2--spec=s")
        t = self.client.get("/chain/next").get_json()
        t = self.client.post("/chain/choose", json={"seq": t["trial"]["seq"], "side": "right", "rt_ms": 5}).get_json()
        self.chain.sessions.clear()                          # what a restart loses
        after = self.client.get("/chain/next").get_json()
        self.assertEqual(after["trial"], t["trial"])
        self.assertEqual(after["next"], t["next"])

    def test_one_session_per_user_and_spec(self):
        self.assertEqual(self.run_session("u1--spec=s"), 10)
        self.assertEqual(self.run_session("u1--spec=s"), 0)                     # same URL: already complete
        self.assertEqual(len(list((Path(self.tmp.name) / "test" / "u1").iterdir())), 1)

    def test_new_sid_in_url_switches_session(self):
        self.app.add_url_rule("/", "home", lambda: str(session.get("username")))
        self.login("u1--spec=s")
        self.assertEqual(self.client.get("/").data, b"u1--spec=s")
        self.assertEqual(self.client.get("/?sid=u1--spec=s").data, b"u1--spec=s")
        self.assertEqual(self.client.get("/?sid=u2--spec=s").data, b"None")  # logged out; Potato logs in the new sid

    def test_admin(self):
        self.run_session()
        self.assertEqual(self.client.get("/chain/admin").status_code, 403)
        page = self.client.get("/chain/admin?key=k")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"u1--spec=s", page.data)
        csv_text = self.client.get("/chain/admin.csv?key=k").data.decode()
        self.assertEqual(len(csv_text.strip().splitlines()), 2)

    def test_admin_states_and_filters(self):
        self.run_session("u1--spec=s")                     # pass (attention item answered left = correct?)
        self.login("u2--spec=s")
        self.client.get("/chain/next")                              # started, no answer yet
        self.login("u3--spec=s")
        self.client.get("/chain/next")
        meta = Path(self.tmp.name) / "test" / "u3" / "spec=s" / "session.json"
        m = json.loads(meta.read_text())
        m["started"] -= 3600                                        # idle for an hour
        meta.write_text(json.dumps(m))
        states = {r["user_id"]: r["state"] for r in self.chain.summaries()}
        self.assertEqual(states["u2"], "running")
        self.assertEqual(states["u3"], "abandoned")
        self.assertIn(states["u1"], ("pass", "fail"))
        csv_rows = lambda q: self.client.get("/chain/admin.csv?key=k" + q).data.decode().strip().splitlines()[1:]
        self.assertEqual(len(csv_rows("&state=abandoned")), 1)
        self.assertEqual(len(csv_rows("&q=U2")), 1)                 # search is case-insensitive
        self.assertEqual(len(csv_rows("&study=" + "a" * 24)), 0)


PID, STUDY, SESS1, SESS2 = "a" * 24, "b" * 24, "c" * 24, "d" * 24


class TestProlific(unittest.TestCase):
    setUp, tearDown, login, run_session = TestServer.setUp, TestServer.tearDown, TestServer.login, TestServer.run_session

    def enter(self, session_id=SESS1, spec="s", pid=PID):
        return self.client.get(f"/?PROLIFIC_PID={pid}&STUDY_ID={STUDY}&SESSION_ID={session_id}&spec={spec}")

    def test_ids(self):
        self.assertIsNone(prolific_ids({}))
        self.assertEqual(prolific_ids({"PROLIFIC_PID": PID, "STUDY_ID": STUDY, "SESSION_ID": SESS1})["SESSION_ID"], SESS1)
        for bad in [{"PROLIFIC_PID": PID}, {"PROLIFIC_PID": "x" * 24, "STUDY_ID": STUDY, "SESSION_ID": SESS1},
                    {"PROLIFIC_PID": PID + "0", "STUDY_ID": STUDY, "SESSION_ID": SESS1}]:
            with self.assertRaises(ValueError):
                prolific_ids(bad)

    def test_entry_and_resume(self):
        r = self.enter()
        self.assertEqual((r.status_code, r.location), (302, f"/?sid={PID}--spec=s"))
        self.assertEqual(self.enter().location, f"/?sid={PID}--spec=s")         # same Prolific session: resume
        self.assertEqual(self.enter(SESS2).location, f"/?sid={PID}--spec=s")    # new Prolific session: same session
        known = json.loads((Path(self.tmp.name) / "test" / PID / "prolific.json").read_text())
        self.assertEqual(set(known), {SESS1, SESS2})                            # both Prolific sessions recorded
        self.assertEqual(self.enter(spec="nope").status_code, 404)
        self.assertEqual(self.enter(pid="nothex").status_code, 400)

    def test_ids_stored_and_redirect_at_the_end(self):
        sid = self.enter().location.split("sid=")[1]
        self.run_session(sid)
        meta = json.loads((Path(self.tmp.name) / "test" / PID / "spec=s" / "session.json").read_text())
        self.assertEqual(meta["prolific"], {"PROLIFIC_PID": PID, "STUDY_ID": STUDY, "SESSION_ID": SESS1})
        self.assertEqual(self.client.get("/chain/next").get_json()["redirect_url"],
                         "https://app.prolific.com/submissions/complete?cc=CODE1")
        self.assertEqual(self.chain.summaries()[0]["prolific_study_id"], STUDY)

    def test_consent_text_for_the_home_icon(self):
        html = self.client.get("/chain/consent_text").get_json()["html"]
        self.assertTrue(html.strip().startswith("<"))
        self.assertNotIn("consent_agree", html)                                  # text only, not the checkbox

    def test_information_sheet_pdf(self):
        r = self.client.get("/information_sheet.pdf")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        self.assertTrue(r.data.startswith(b"%PDF"))
        r.close()


if __name__ == "__main__":
    unittest.main()
