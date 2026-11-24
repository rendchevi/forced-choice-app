"""Flask blueprint that serves the typicality items to the jsPsych page and stores every rating.

Identity comes only from the Potato login (``session["username"]`` = the session id),
never from the request body. Storage, one folder per session:

    {output_root}/{study_id}/{user_id}/chain={k}/session.json   summary, rewritten each rating
    {output_root}/{study_id}/{user_id}/chain={k}/trials.jsonl   one line per rating
"""

import csv
import html
import io
import json
import logging
import math
import os
import statistics
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, request, session

from typ_core import Session, load_assets, parse_sid

log = logging.getLogger(__name__)

MAX_MS = 24 * 3600 * 1000


def _write_json_atomic(path: Path, obj):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _ms(v) -> bool:
    return v is None or (type(v) in (int, float) and math.isfinite(v) and 0 <= v <= MAX_MS)


class TypStudy:
    def __init__(self, study: dict):
        self.study = study
        self.root = Path(study["output_root"]) / study["study_id"]
        self.assets = load_assets(study["assets_root"], study["stimuli_name"])  # fails before anyone arrives
        self.sessions = {}   # sid -> (Session, meta)
        # shortcut: one lock for all sessions; per-session locks if many hundreds answer at the same moment
        self.lock = threading.Lock()

    def folder(self, user_id, chain) -> Path:
        return self.root / user_id / f"chain={chain}"

    def get(self, sid):
        """The live session for this sid, created or rebuilt from disk on first use. Caller holds the lock."""
        if sid in self.sessions:
            return self.sessions[sid]
        parsed = parse_sid(sid)
        if not parsed:
            abort(400, "malformed session id; expected <user_id>--chain=<k>")
        user_id, chain = parsed
        s = Session(self.study, self.assets, sid)
        folder = self.folder(user_id, chain)
        meta_path = folder / "session.json"
        scale = {k: self.study[k] for k in ("question", "min_value", "max_value", "step", "left_label", "right_label")}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["scale"] != scale or meta["stimuli_name"] != self.study["stimuli_name"]:
                abort(409, "the study settings changed since this session started; use a new chain number")
            if meta["asset_sha256"] != self.assets.sha256:
                log.warning("%s: asset files changed since the session started", sid)
            lines = (folder / "trials.jsonl").read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines if line.strip()]
            s.replay(records)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "trials.jsonl").touch()
            meta = {"study_id": self.study["study_id"], "sid": sid, "user_id": user_id, "chain": chain,
                    "stimuli_name": self.study["stimuli_name"], "scale": scale, "asset_sha256": self.assets.sha256,
                    "status": "in_progress", "started": time.time(), "finished": None,
                    "n_trials": s.n_trials, "n_trials_done": 0, "attention_correct": 0,
                    "attention_total": sum(it["trial_type"] == "attention" for it in s.items), "median_rt_ms": None}
            records = []
            _write_json_atomic(meta_path, meta)
        meta["_rts"] = [r["rt_ms"] for r in records if r["trial_type"] == "stimulus" and r["rt_ms"] is not None]
        self.sessions[sid] = (s, meta)
        return self.sessions[sid]

    def start_payload(self, s: Session):
        if s.done:
            return {"done": True, "completion_code": self.study["completion_code"]}
        st = self.study
        return {"done": False, "question": st["question"], "min_value": st["min_value"], "max_value": st["max_value"],
                "step": st["step"], "left_label": st["left_label"], "right_label": st["right_label"],
                "i_trial": s.i_trial, "items": [it["text"] for it in s.items]}

    def record(self, sid, s: Session, meta: dict, value, rt_ms, first_touch_ms):
        now = time.time()
        rec = s.rate(value)
        prev = meta.get("_prev")
        meta["_prev"] = now
        rec.update(user_id=meta["user_id"], sid=sid, rt_ms=rt_ms, first_touch_ms=first_touch_ms,
                   server_ms=round((now - prev) * 1000) if prev else None, t_answered=now)
        folder = self.folder(meta["user_id"], meta["chain"])
        with open(folder / "trials.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        meta["n_trials_done"] += 1
        if rec["trial_type"] == "attention":
            meta["attention_correct"] += bool(rec["correct"])
        elif rt_ms is not None:
            meta["_rts"].append(rt_ms)
            meta["median_rt_ms"] = statistics.median(meta["_rts"])
        if s.done:
            meta.update(status="complete", finished=now)
        _write_json_atomic(folder / "session.json", {k: v for k, v in meta.items() if not k.startswith("_")})

    def summaries(self):
        rows = []
        for p in sorted(self.root.glob("*/*/session.json")):  # shortcut: scans files per request; index if slow
            m = json.loads(p.read_text(encoding="utf-8"))
            rows.append({"user_id": m["user_id"], "chain": m["chain"], "status": m["status"],
                         "rated": f'{m["n_trials_done"]}/{m["n_trials"]}',
                         "attention_correct": m["attention_correct"], "attention_total": m["attention_total"],
                         "median_rt_ms": m["median_rt_ms"], "started": _fmt_time(m["started"]),
                         "finished": _fmt_time(m["finished"]), "sid": m["sid"]})
        return rows


def _fmt_time(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def make_blueprint(typ: TypStudy, is_admin) -> Blueprint:
    """``is_admin(key) -> bool`` guards the monitoring routes."""
    bp = Blueprint("typ", __name__)

    @bp.before_app_request
    def switch_session_from_url():
        # Potato's URL login only runs when the browser is not logged in yet, so a participant
        # opening the URL of their next session would land back in the previous one.
        sid = request.args.get("sid")
        if request.path != "/" or sid is None:
            return
        if not parse_sid(sid):
            abort(400, "malformed session id; expected <user_id>--chain=<k>")
        if session.get("username") not in (None, sid):
            session.clear()

    def current_sid():
        sid = session.get("username")
        if not sid:
            abort(401, "not logged in")
        return sid

    @bp.get("/typ/start")
    def start():
        sid = current_sid()
        with typ.lock:
            s, meta = typ.get(sid)
            meta["_prev"] = time.time()  # page (re)load: the participant's clock restarts too
            return jsonify(typ.start_payload(s))

    @bp.post("/typ/rate")
    def rate():
        sid = current_sid()
        body = request.get_json(silent=True) or {}
        i_trial, value = body.get("i_trial"), body.get("value")
        rt_ms, first_touch_ms = body.get("rt_ms"), body.get("first_touch_ms")
        if type(i_trial) is not int or not _ms(rt_ms) or not _ms(first_touch_ms):
            abort(400, "need an integer i_trial; rt_ms and first_touch_ms are milliseconds")
        with typ.lock:
            s, meta = typ.get(sid)
            if s.done or i_trial != s.i_trial:
                # stale or repeated answer (double click, second tab): ignore it, tell the page where we are
                return jsonify({"stale": True, "done": s.done, "i_trial": s.i_trial,
                                "completion_code": typ.study["completion_code"] if s.done else None}), 409
            if not s.valid_value(value):
                abort(400, "value outside the scale or off its step")
            typ.record(sid, s, meta, value, rt_ms, first_touch_ms)
            return jsonify({"done": s.done, "i_trial": s.i_trial,
                            "completion_code": typ.study["completion_code"] if s.done else None})

    def require_admin():
        if not is_admin(request.args.get("key")):
            abort(403, "admin key required: ?key=<contents of admin_api_key.txt>")

    @bp.get("/typ/admin.csv")
    def admin_csv():
        require_admin()
        rows = typ.summaries()
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["user_id"])
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=sessions--{typ.study['study_id']}.csv"})

    @bp.get("/typ/admin")
    def admin():
        require_admin()
        rows = typ.summaries()
        cols = list(rows[0]) if rows else []
        head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        body = "".join(
            f'<tr data-state="{_state(r)}">' + "".join(f"<td>{html.escape('' if r[c] is None else str(r[c]))}</td>" for c in cols) + "</tr>"
            for r in rows)
        key = html.escape(request.args.get("key", ""), quote=True)
        return ADMIN_HTML.format(study=html.escape(typ.study["study_id"]), n=len(rows), head=head, body=body, key=key)

    return bp


def _state(r):
    if r["status"] != "complete":
        return "running"
    return "pass" if r["attention_correct"] == r["attention_total"] else "fail"


ADMIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Typicality sessions</title>
<style>body{{font:14px system-ui,sans-serif;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}tr[data-state=fail]{{background:#fde2e2}}
tr[data-state=pass]{{background:#e3f6e3}}tr[data-state=running]{{background:#fff7d6}}</style></head><body>
<h2>Study {study}: {n} sessions</h2>
<p>Show: <select id="f"><option value="">all</option><option value="pass">all checks right</option>
<option value="fail">some check wrong</option><option value="running">running</option></select>
&nbsp;<a href="/typ/admin.csv?key={key}">download CSV</a></p>
<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
<script>document.getElementById('f').onchange=function(e){{var v=e.target.value;
document.querySelectorAll('tbody tr').forEach(function(r){{r.style.display=!v||r.dataset.state===v?'':'none';}});}};</script>
</body></html>"""
