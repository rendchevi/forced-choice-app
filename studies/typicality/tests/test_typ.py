"""CPU-only tests for the typicality logic and endpoints.   python -m unittest discover -s tests -v"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask, render_template_string, session

from typ_core import Session, load_assets, load_study, parse_sid
from typ_server import TypStudy, make_blueprint

ASSETS = ROOT.parents[1] / "assets" / "stimuli"   # repo-level shared assets
STUDY = {"study_id": "test", "stimuli_name": "dummy_emosent_binary", "assets_root": str(ASSETS),
         "question": "q?", "min_value": 0, "max_value": 100, "step": 1, "left_label": "lo", "right_label": "hi",
         "completion_code": "CODE9", "potato_config": "config.yaml", "output_root": "", "host": "x", "port": 0,
         "threads": 1, "no_consent_code": "NOC", "abandon_after_min": 30}


def new_session(sid="u1--run=1", **kw):
    st = {**STUDY, **kw}
    return Session(st, load_assets(st["assets_root"], st["stimuli_name"]), sid)


def answer(s, it, right=True):
    """A rating that passes (or fails) an attention item; mid-scale for a stimulus."""
    if it["trial_type"] == "stimulus":
        return 42
    lo, hi = s.study["min_value"], s.study["max_value"]
    want = hi if it["answer"] == "max" else lo
    return want if right else (lo + hi) / 2


class TestCore(unittest.TestCase):
    def test_study_file_loads(self):
        st = load_study(ROOT / "study.yaml")
        self.assertLess(st["min_value"], st["max_value"])

    def test_bad_scale_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.yaml"
            for bad in ({"min_value": 5, "max_value": 5}, {"step": 0}, {"step": 7}, {"extra_key": 1}):
                p.write_text(json.dumps({**STUDY, **bad}))
                with self.assertRaises(ValueError, msg=bad):
                    load_study(p)

    def test_parse_sid(self):
        self.assertEqual(parse_sid("u-1_a--run=2"), ("u-1_a", 2))
        for bad in ["u1", "u1--run=x", "u--x--run=1", "u1--run=1\n", "../u--run=1", "u1--spec=a--run=1"]:
            self.assertIsNone(parse_sid(bad), bad)

    def test_every_item_once_attention_spread(self):
        s = new_session()
        kinds = [it["trial_type"] for it in s.items]
        self.assertEqual(kinds.count("stimulus"), 23)
        self.assertEqual(kinds.count("attention"), 6)
        before = [kinds[:i].count("stimulus") for i, k in enumerate(kinds) if k == "attention"]
        self.assertEqual(before, [3, 6, 9, 13, 16, 19])
        self.assertNotEqual([it["text"] for it in s.items], [it["text"] for it in new_session("u2--run=1").items])

    def test_values_checked(self):
        s = new_session(step=0.5)
        for bad in [-1, 101, 3.3, float("nan"), "50", None, True]:
            self.assertFalse(s.valid_value(bad), bad)
        for good in [0, 100, 3.5, 50]:
            self.assertTrue(s.valid_value(good), good)

    def test_attention_graded_at_the_right_end(self):
        s = new_session()
        recs = []
        while not s.done:
            it = s.items[s.i_trial]
            recs.append(s.rate(answer(s, it, right=it["att_id"] != "att_typ01")))
        att = {r["att_id"]: r["correct"] for r in recs if r["trial_type"] == "attention"}
        self.assertEqual(att.pop("att_typ01"), False)
        self.assertTrue(all(att.values()))
        self.assertTrue(all(r["correct"] is None for r in recs if r["trial_type"] == "stimulus"))

    def test_replay(self):
        a = new_session()
        recs = [a.rate(answer(a, a.items[a.i_trial])) for _ in range(7)]
        b = new_session()
        b.replay(recs)
        self.assertEqual(b.i_trial, 7)
        with self.assertRaises(ValueError):
            new_session("u2--run=1").replay(recs)   # another order


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.typ = TypStudy({**STUDY, "output_root": self.tmp.name})
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(make_blueprint(self.typ, lambda key: key == "k"))
        self.app.add_url_rule("/", "home", lambda: str(session.get("username")))
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, sid):
        with self.client.session_transaction() as sess:
            sess["username"] = sid

    def rate_all(self, sid="u1--run=1"):
        self.login(sid)
        st = self.client.get("/typ/start").get_json()
        s = self.typ.sessions[sid][0]
        r = None
        for i in range(st["i_trial"], len(st["items"])):
            r = self.client.post("/typ/rate", json={"i_trial": i, "value": answer(s, s.items[i]),
                                                    "rt_ms": 900, "first_touch_ms": 400})
            self.assertEqual(r.status_code, 200, r.data)
        return r.get_json()

    def test_needs_login_and_valid_sid(self):
        self.assertEqual(self.client.get("/typ/start").status_code, 401)
        self.login("nope")
        self.assertEqual(self.client.get("/typ/start").status_code, 400)

    def test_full_session(self):
        last = self.rate_all()
        self.assertEqual(last, {"done": True, "i_trial": 29, "completion_code": "CODE9", "redirect_url": None})
        folder = Path(self.tmp.name) / "test" / "u1" / "run=1"
        meta = json.loads((folder / "session.json").read_text())
        self.assertEqual((meta["status"], meta["n_trials_done"], meta["attention_correct"], meta["attention_total"]),
                         ("complete", 29, 6, 6))
        self.assertEqual(len((folder / "trials.jsonl").read_text().splitlines()), 29)
        self.assertEqual(self.client.get("/typ/start").get_json(),
                         {"done": True, "completion_code": "CODE9", "redirect_url": None})  # test link: no Prolific

    def test_code_only_when_done(self):
        self.login("u1--run=1")
        self.assertNotIn("completion_code", self.client.get("/typ/start").get_json())

    def test_bad_input_and_stale(self):
        self.login("u1--run=1")
        self.client.get("/typ/start")
        for body in [{"i_trial": 0, "value": 101}, {"i_trial": 0, "value": "5"}, {"i_trial": "0", "value": 5},
                     {"i_trial": 0, "value": 5, "rt_ms": -1}, {"i_trial": 0, "value": 5, "first_touch_ms": "x"}]:
            self.assertEqual(self.client.post("/typ/rate", json=body).status_code, 400, body)
        s = self.typ.sessions["u1--run=1"][0]
        ok = {"i_trial": 0, "value": answer(s, s.items[0]), "rt_ms": 5, "first_touch_ms": None}
        self.assertEqual(self.client.post("/typ/rate", json=ok).status_code, 200)
        again = self.client.post("/typ/rate", json=ok)                  # double click
        self.assertEqual((again.status_code, again.get_json()["i_trial"]), (409, 1))

    def test_resume_after_restart(self):
        self.login("u2--run=1")
        st = self.client.get("/typ/start").get_json()
        s = self.typ.sessions["u2--run=1"][0]
        for i in range(4):
            self.client.post("/typ/rate", json={"i_trial": i, "value": answer(s, s.items[i]), "rt_ms": 1})
        self.typ.sessions.clear()                                         # what a restart loses
        again = self.client.get("/typ/start").get_json()
        self.assertEqual((again["i_trial"], again["items"]), (4, st["items"]))

    def test_new_sid_in_url_switches_session(self):
        self.login("u1--run=1")
        self.assertEqual(self.client.get("/?sid=u1--run=1").data, b"u1--run=1")
        self.assertEqual(self.client.get("/?sid=u1--run=2").data, b"None")
        self.assertEqual(self.client.get("/?sid=bad").status_code, 400)

    def test_admin(self):
        self.rate_all()
        self.assertEqual(self.client.get("/typ/admin").status_code, 403)
        page = self.client.get("/typ/admin?key=k")
        self.assertIn(b'data-state="pass"', page.data)
        self.assertEqual(len(self.client.get("/typ/admin.csv?key=k").data.decode().strip().splitlines()), 2)

    def test_admin_abandoned_and_filters(self):
        self.rate_all("u1--run=1")
        self.login("u2--run=1")
        self.client.get("/typ/start")
        meta = Path(self.tmp.name) / "test" / "u2" / "run=1" / "session.json"
        m = json.loads(meta.read_text())
        m["started"] -= 3600
        meta.write_text(json.dumps(m))
        self.assertEqual({r["user_id"]: r["state"] for r in self.typ.summaries()}, {"u1": "pass", "u2": "abandoned"})
        rows = lambda q: self.client.get("/typ/admin.csv?key=k" + q).data.decode().strip().splitlines()[1:]
        self.assertEqual((len(rows("&state=pass")), len(rows("&q=u2")), len(rows("&state=running"))), (1, 1, 0))


PID, PSTUDY, SESS1, SESS2 = "a" * 24, "b" * 24, "c" * 24, "d" * 24


class TestProlific(unittest.TestCase):
    setUp, tearDown, login, rate_all = TestServer.setUp, TestServer.tearDown, TestServer.login, TestServer.rate_all

    def enter(self, session_id=SESS1, pid=PID):
        return self.client.get(f"/?PROLIFIC_PID={pid}&STUDY_ID={PSTUDY}&SESSION_ID={session_id}")

    def test_entry_assigns_and_resumes(self):
        self.assertEqual(self.enter().location, f"/?sid={PID}--run=1")
        self.assertEqual(self.enter().location, f"/?sid={PID}--run=1")
        self.assertEqual(self.enter(SESS2).location, f"/?sid={PID}--run=2")
        self.assertEqual(self.enter(pid="A" * 24).status_code, 400)       # Prolific ids are lowercase hex

    def test_ids_stored_and_redirect_at_the_end(self):
        sid = self.enter().location.split("sid=")[1]
        last = self.rate_all(sid)
        self.assertEqual(last["redirect_url"], "https://app.prolific.com/submissions/complete?cc=CODE9")
        meta = json.loads((Path(self.tmp.name) / "test" / PID / "run=1" / "session.json").read_text())
        self.assertEqual(meta["prolific"]["SESSION_ID"], SESS1)

    def test_no_consent_exit_link(self):
        with self.app.test_request_context("/"):
            session["username"] = f"{PID}--run=1"
            self.assertEqual(render_template_string("{{ exit_url }}"), "https://app.prolific.com/submissions/complete?cc=NOC")


if __name__ == "__main__":
    unittest.main()
