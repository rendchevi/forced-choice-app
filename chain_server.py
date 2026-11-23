"""Flask blueprint that serves the chain to the jsPsych page and stores every answer.

Identity comes only from the Potato login (``session["username"]`` = the session id),
never from the request body. Storage, one folder per session:

    {output_root}/{study_id}/{user_id}/spec={spec}--chain={k}/session.json   summary, rewritten each trial
    {output_root}/{study_id}/{user_id}/spec={spec}--chain={k}/trials.jsonl   one line per answered trial
"""

import html
import json
import logging
import math
import os
import statistics
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, request, session

from chain_core import (SIDES, Session, build_proposal, load_assets, parse_sid,
                        session_seed)

log = logging.getLogger(__name__)

MAX_RT_MS = 24 * 3600 * 1000


def _write_json_atomic(path: Path, obj):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class ChainStudy:
    def __init__(self, study: dict, specs: dict):
        self.study_id = study["study_id"]
        self.root = Path(study["output_root"]) / self.study_id
        self.specs = specs
        self.assets, self.proposals = {}, {}
        for spec in specs.values():  # load everything at boot so a bad spec fails before anyone arrives
            self.assets[spec.name] = load_assets(study["assets_root"], spec.stimuli_name, spec.graph_name, spec.n_attention)
            self.proposals[spec.name] = build_proposal(spec, self.assets[spec.name])
            for w in self.proposals[spec.name].check():
                log.warning("spec %s: %s", spec.name, w)
        self.sessions = {}   # sid -> (Session, meta dict)
        # shortcut: one lock for all sessions; per-session locks if many hundreds answer at the same moment
        self.lock = threading.Lock()

    def folder(self, user_id, spec_name, chain) -> Path:
        return self.root / user_id / f"spec={spec_name}--chain={chain}"

    def get(self, sid):
        """The live session for this sid, created or rebuilt from disk on first use. Caller holds the lock."""
        if sid in self.sessions:
            return self.sessions[sid]
        parsed = parse_sid(sid)
        if not parsed:
            abort(400, "malformed session id")
        user_id, spec_name, chain = parsed
        spec = self.specs.get(spec_name)
        if spec is None:
            abort(404, f"unknown spec {spec_name!r}")
        seed = session_seed(self.study_id, user_id, spec_name, chain)
        s = Session(spec, self.assets[spec_name], self.proposals[spec_name], seed)
        folder = self.folder(user_id, spec_name, chain)
        meta_path = folder / "session.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["spec"] != spec.__dict__ or meta["seed"] != seed:
                abort(409, "the spec changed since this session started; use a new chain number or spec name")
            if meta["asset_sha256"] != self.assets[spec_name].sha256:
                log.warning("%s: asset files changed since the session started", sid)
            records = [json.loads(line) for line in (folder / "trials.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            s.replay(records)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "trials.jsonl").touch()
            meta = {"study_id": self.study_id, "sid": sid, "user_id": user_id, "chain": chain, "seed": seed,
                    "spec": spec.__dict__, "asset_sha256": self.assets[spec_name].sha256,
                    "status": "in_progress", "started": time.time(), "finished": None,
                    "n_trials": s.n_trials, "n_trials_done": 0, "n_draw_done": 0,
                    "attention_correct": 0, "attention_done": 0, "attention_passed": None, "median_rt_ms": None}
            records = []
            _write_json_atomic(meta_path, meta)
        meta["_rts"] = [r["rt_ms"] for r in records if r["trial_type"] == "chain" and r["rt_ms"] is not None]
        self.sessions[sid] = (s, meta)
        return self.sessions[sid]

    def payload(self, s: Session, meta: dict):
        if s.done:
            return {"done": True}
        t = s.pending
        meta["_served"] = time.time()  # a reload re-serves the trial and restarts its server clock
        return {"done": False, "i_trial": t["i_trial"], "n_trials": s.n_trials, "question": s.spec.question,
                "left": t["left_text"], "right": t["right_text"]}

    def record(self, sid, s: Session, meta: dict, side: str, rt_ms):
        now = time.time()
        rec = s.choose(side)
        served = meta.get("_served")
        rec.update(user_id=meta["user_id"], sid=sid, rt_ms=rt_ms,
                   server_ms=round((now - served) * 1000) if served else None,
                   t_served=served, t_answered=now)
        folder = self.folder(meta["user_id"], meta["spec"]["name"], meta["chain"])
        with open(folder / "trials.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        meta["n_trials_done"] += 1
        if rec["trial_type"] == "chain":
            meta["n_draw_done"] += 1
            if rt_ms is not None:
                meta["_rts"].append(rt_ms)
                meta["median_rt_ms"] = statistics.median(meta["_rts"])
        else:
            meta["attention_done"] += 1
            meta["attention_correct"] += bool(rec["correct"])
        if s.done:
            meta.update(status="complete", finished=now,
                        attention_passed=meta["attention_correct"] >= s.spec.attention_pass)
        _write_json_atomic(folder / "session.json", {k: v for k, v in meta.items() if not k.startswith("_")})

    def summaries(self):
        rows = []
        for p in sorted(self.root.glob("*/*/session.json")):  # shortcut: scans files per request; index if slow
            m = json.loads(p.read_text(encoding="utf-8"))
            rows.append({"user_id": m["user_id"], "spec": m["spec"]["name"], "chain": m["chain"], "status": m["status"],
                         "draws": f'{m["n_draw_done"]}/{m["spec"]["n_draw"]}',
                         "attention": f'{m["attention_correct"]}/{m["attention_done"]} (need {m["spec"]["attention_pass"]}/{m["spec"]["n_attention"]})',
                         "attention_passed": m["attention_passed"], "median_rt_ms": m["median_rt_ms"],
                         "started": _fmt_time(m["started"]), "finished": _fmt_time(m["finished"]), "sid": m["sid"]})
        return rows


def _fmt_time(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def make_blueprint(chain: ChainStudy, is_admin) -> Blueprint:
    """``is_admin(key) -> bool`` guards the monitoring routes."""
    bp = Blueprint("chain", __name__)

    def current_sid():
        sid = session.get("username")
        if not sid:
            abort(401, "not logged in")
        return sid

    @bp.get("/chain/next")
    def next_trial():
        sid = current_sid()
        with chain.lock:
            s, meta = chain.get(sid)
            return jsonify(chain.payload(s, meta))

    @bp.post("/chain/choose")
    def choose():
        sid = current_sid()
        body = request.get_json(silent=True) or {}
        side, i_trial, rt_ms = body.get("side"), body.get("i_trial"), body.get("rt_ms")
        if side not in SIDES or type(i_trial) is not int:
            abort(400, "need side in ('left','right') and an integer i_trial")
        if rt_ms is not None and (type(rt_ms) not in (int, float) or not math.isfinite(rt_ms) or not 0 <= rt_ms <= MAX_RT_MS):
            abort(400, "rt_ms must be a number of milliseconds")
        with chain.lock:
            s, meta = chain.get(sid)
            if s.done or i_trial != s.pending["i_trial"]:
                # stale or repeated answer (double click, second tab): ignore it and resend the current state
                resp = chain.payload(s, meta)
                resp["stale"] = True
                return jsonify(resp), 409
            chain.record(sid, s, meta, side, rt_ms)
            return jsonify(chain.payload(s, meta))

    def require_admin():
        if not is_admin(request.args.get("key")):
            abort(403, "admin key required: ?key=<contents of admin_api_key.txt>")

    @bp.get("/chain/admin.csv")
    def admin_csv():
        require_admin()
        import csv
        import io
        rows = chain.summaries()
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["user_id"])
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=sessions--{chain.study_id}.csv"})

    @bp.get("/chain/admin")
    def admin():
        require_admin()
        rows = chain.summaries()
        cols = list(rows[0]) if rows else []
        head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        body = "".join(
            f'<tr data-state="{_state(r)}">' + "".join(f"<td>{html.escape('' if r[c] is None else str(r[c]))}</td>" for c in cols) + "</tr>"
            for r in rows)
        key = html.escape(request.args.get("key", ""), quote=True)
        return ADMIN_HTML.format(study=html.escape(chain.study_id), n=len(rows), head=head, body=body, key=key)

    return bp


def _state(r):
    if r["status"] != "complete":
        return "running"
    return "pass" if r["attention_passed"] else "fail"


ADMIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Chain sessions</title>
<style>body{{font:14px system-ui,sans-serif;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}tr[data-state=fail]{{background:#fde2e2}}
tr[data-state=pass]{{background:#e3f6e3}}tr[data-state=running]{{background:#fff7d6}}</style></head><body>
<h2>Study {study}: {n} sessions</h2>
<p>Show: <select id="f"><option value="">all</option><option>pass</option><option>fail</option><option>running</option></select>
&nbsp;<a href="/chain/admin.csv?key={key}">download CSV</a> &nbsp;Approve/reject on Prolific: <a href="/admin">Potato admin</a></p>
<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
<script>document.getElementById('f').onchange=function(e){{var v=e.target.value;
document.querySelectorAll('tbody tr').forEach(function(r){{r.style.display=!v||r.dataset.state===v?'':'none';}});}};</script>
</body></html>"""
