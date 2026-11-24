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
from flask import Flask, render_template_string, session

from chain_core import (Session, Spec, attention_after, load_assets, parse_sid,
                        session_seed)
from chain_server import ChainStudy, make_blueprint
from prolific import prolific_ids
from proposal import load_proposal_class
from proposal.graph.dmcmcp import DMCMCP

ASSETS = ROOT.parents[1] / "assets" / "stimuli"   # repo-level shared assets
STIM = "dummy_emosent_binary"


def spec(**kw):
    base = dict(name="t", stimuli_name=STIM, graph_name="b=4--emb=spades", propose_fn="graph/dmcmcp",
                n_draw=20, question="q?", n_attention=3, attention_pass=3)
    return Spec(**{**base, **kw})


def new_session(seed=1, **kw):
    sp = spec(**kw)
    assets = load_assets(ASSETS, sp.stimuli_name, sp.graph_name, sp.n_attention)
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
                 "no_consent_code": "NOC", "abandon_after_min": 30}
        self.specs = {"s": spec(name="s", n_draw=4, n_attention=1, attention_pass=1, completion_code="CODE1")}
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
            t = self.client.post("/chain/choose", json={"i_trial": t["trial"]["i_trial"], "side": "left", "rt_ms": 1234}).get_json()
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

    def test_prefetch_is_what_the_server_then_serves(self):
        self.login("u3--spec=s--chain=1")
        t = self.client.get("/chain/next").get_json()
        while not t["done"]:
            side = "right" if t["trial"]["i_trial"] % 2 else "left"
            promised = t["next"][side]
            t = self.client.post("/chain/choose", json={"i_trial": t["trial"]["i_trial"], "side": side, "rt_ms": 1}).get_json()
            if t["done"]:
                self.assertIsNone(promised)
            else:
                self.assertEqual(t["trial"], promised)

    def test_completion_code_only_when_done(self):
        self.login("u4--spec=s--chain=1")
        t = self.client.get("/chain/next").get_json()
        self.assertNotIn("completion_code", t)
        self.run_session("u4--spec=s--chain=1")
        self.assertEqual(self.client.get("/chain/next").get_json(),
                         {"done": True, "completion_code": "CODE1", "redirect_url": None})  # test link: no Prolific

    def test_bad_input_and_stale_answer(self):
        self.login("u1--spec=s--chain=1")
        t = self.client.get("/chain/next").get_json()
        for body in [{"i_trial": 0, "side": "up"}, {"i_trial": "0", "side": "left"},
                     {"i_trial": 0, "side": "left", "rt_ms": -5}, {"i_trial": 0, "side": "left", "rt_ms": "fast"}]:
            self.assertEqual(self.client.post("/chain/choose", json=body).status_code, 400, body)
        ok = self.client.post("/chain/choose", json={"i_trial": t["trial"]["i_trial"], "side": "left", "rt_ms": 10})
        self.assertEqual(ok.status_code, 200)
        again = self.client.post("/chain/choose", json={"i_trial": t["trial"]["i_trial"], "side": "left", "rt_ms": 10})
        self.assertEqual(again.status_code, 409)            # double click is not recorded twice
        self.assertEqual(again.get_json()["trial"]["i_trial"], t["trial"]["i_trial"] + 1)

    def test_resume_after_restart(self):
        self.login("u2--spec=s--chain=1")
        t = self.client.get("/chain/next").get_json()
        t = self.client.post("/chain/choose", json={"i_trial": t["trial"]["i_trial"], "side": "right", "rt_ms": 5}).get_json()
        self.chain.sessions.clear()                          # what a restart loses
        after = self.client.get("/chain/next").get_json()
        self.assertEqual(after["trial"], t["trial"])
        self.assertEqual(after["next"], t["next"])

    def test_chains_are_separate(self):
        self.run_session("u1--spec=s--chain=1")
        self.run_session("u1--spec=s--chain=2")
        self.assertEqual(len(list((Path(self.tmp.name) / "test" / "u1").iterdir())), 2)

    def test_new_sid_in_url_switches_session(self):
        self.app.add_url_rule("/", "home", lambda: str(session.get("username")))
        self.login("u1--spec=s--chain=1")
        self.assertEqual(self.client.get("/").data, b"u1--spec=s--chain=1")
        self.assertEqual(self.client.get("/?sid=u1--spec=s--chain=1").data, b"u1--spec=s--chain=1")
        self.assertEqual(self.client.get("/?sid=u1--spec=s--chain=2").data, b"None")  # logged out; Potato logs in the new sid

    def test_admin(self):
        self.run_session()
        self.assertEqual(self.client.get("/chain/admin").status_code, 403)
        page = self.client.get("/chain/admin?key=k")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"u1--spec=s--chain=1", page.data)
        csv_text = self.client.get("/chain/admin.csv?key=k").data.decode()
        self.assertEqual(len(csv_text.strip().splitlines()), 2)

    def test_admin_states_and_filters(self):
        self.run_session("u1--spec=s--chain=1")                     # pass (attention item answered left = correct?)
        self.login("u2--spec=s--chain=1")
        self.client.get("/chain/next")                              # started, no answer yet
        self.login("u3--spec=s--chain=1")
        self.client.get("/chain/next")
        meta = Path(self.tmp.name) / "test" / "u3" / "spec=s--chain=1" / "session.json"
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

    def test_entry_assigns_and_resumes_chains(self):
        r = self.enter()
        self.assertEqual((r.status_code, r.location), (302, f"/?sid={PID}--spec=s--chain=1"))
        self.assertEqual(self.enter().location, f"/?sid={PID}--spec=s--chain=1")       # same Prolific session: resume
        self.assertEqual(self.enter(SESS2).location, f"/?sid={PID}--spec=s--chain=2")  # new Prolific session: new chain
        self.assertEqual(self.enter(spec="nope").status_code, 404)
        self.assertEqual(self.enter(pid="nothex").status_code, 400)

    def test_entry_skips_chains_used_by_test_links(self):
        self.run_session(f"{PID}--spec=s--chain=1")
        self.assertEqual(self.enter().location, f"/?sid={PID}--spec=s--chain=2")

    def test_ids_stored_and_redirect_at_the_end(self):
        sid = self.enter().location.split("sid=")[1]
        self.run_session(sid)
        meta = json.loads((Path(self.tmp.name) / "test" / PID / "spec=s--chain=1" / "session.json").read_text())
        self.assertEqual(meta["prolific"], {"PROLIFIC_PID": PID, "STUDY_ID": STUDY, "SESSION_ID": SESS1})
        self.assertEqual(self.client.get("/chain/next").get_json()["redirect_url"],
                         "https://app.prolific.com/submissions/complete?cc=CODE1")
        self.assertEqual(self.chain.summaries()[0]["prolific_study_id"], STUDY)

    def test_no_consent_exit_link(self):
        page = "{{ exit_url | default('') }}"
        with self.app.test_request_context("/"):
            session["username"] = f"{PID}--spec=s--chain=1"
            self.assertEqual(render_template_string(page), "https://app.prolific.com/submissions/complete?cc=NOC")
            session["username"] = "u1--spec=s--chain=1"
            self.assertEqual(render_template_string(page), "")


if __name__ == "__main__":
    unittest.main()
