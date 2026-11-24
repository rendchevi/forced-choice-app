"""Flask blueprint that serves the typicality items to the jsPsych page and stores every rating.

Identity comes only from the Potato login (``session["username"]`` = the session id),
never from the request body. Storage, one folder per session:

    {output_root}/{study_id}/{user_id}/run={k}/session.json   summary, rewritten each rating
    {output_root}/{study_id}/{user_id}/run={k}/trials.jsonl   one line per rating
    {output_root}/{study_id}/{user_id}/prolific.json            Prolific SESSION_ID -> sid (prolific.py)
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

from flask import Blueprint, Response, abort, jsonify, redirect, request, session

from prolific import assign_sid, completion_url, ids_for_sid, is_prolific_user, prolific_ids
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
        self.abandon_after_s = 60 * study["abandon_after_min"]
        self.assets = load_assets(study["assets_root"], study["stimuli_name"])  # fails before anyone arrives
        self.sessions = {}   # sid -> (Session, meta)
        # guards self.sessions and prolific.json only; each session's answers use its own meta["_lock"],
        # so the fsync of one session's answer never holds up another session
        self.lock = threading.Lock()

    def folder(self, user_id, run) -> Path:
        return self.root / user_id / f"run={run}"

    def used_runs(self, user_id):
        """Run numbers this user already has (session folders on disk)."""
        return [int(p.name.split("=", 1)[1]) for p in (self.root / user_id).glob("run=*")]

    def done_payload(self, meta: dict) -> dict:
        code = self.study["completion_code"]
        return {"done": True, "completion_code": code,
                "redirect_url": completion_url(code) if meta.get("prolific") and code else None}

    def get(self, sid):
        """The live session for this sid, created or rebuilt from disk on first use. Caller holds the lock."""
        if sid in self.sessions:
            return self.sessions[sid]
        parsed = parse_sid(sid)
        if not parsed:
            abort(400, "malformed session id; expected <user_id>--run=<k>")
        user_id, run = parsed
        s = Session(self.study, self.assets, sid)
        folder = self.folder(user_id, run)
        meta_path = folder / "session.json"
        scale = {k: self.study[k] for k in ("question", "min_value", "max_value", "step", "left_label", "right_label")}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["scale"] != scale or meta["stimuli_name"] != self.study["stimuli_name"]:
                abort(409, "the study settings changed since this session started; use a new run number")
            if meta["asset_sha256"] != self.assets.sha256:
                log.warning("%s: asset files changed since the session started", sid)
            lines = (folder / "trials.jsonl").read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines if line.strip()]
            s.replay(records)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "trials.jsonl").touch()
            meta = {"study_id": self.study["study_id"], "sid": sid, "user_id": user_id, "run": run,
                    "stimuli_name": self.study["stimuli_name"], "scale": scale, "asset_sha256": self.assets.sha256,
                    "prolific": ids_for_sid(self.root, user_id, sid),
                    "status": "in_progress", "started": time.time(), "updated": None, "finished": None,
                    "n_trials": s.n_trials, "n_trials_done": 0, "attention_correct": 0,
                    "attention_total": sum(it["trial_type"] == "attention" for it in s.items), "median_rt_ms": None}
            records = []
            _write_json_atomic(meta_path, meta)
        meta["_lock"] = threading.Lock()   # one answer at a time per session; sessions run in parallel
        meta["_rts"] = [r["rt_ms"] for r in records if r["trial_type"] == "stimulus" and r["rt_ms"] is not None]
        self.sessions[sid] = (s, meta)
        return self.sessions[sid]

    def start_payload(self, s: Session, meta: dict):
        if s.done:
            return self.done_payload(meta)
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
        folder = self.folder(meta["user_id"], meta["run"])
        with open(folder / "trials.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        meta["n_trials_done"] += 1
        meta["updated"] = now
        if rec["trial_type"] == "attention":
            meta["attention_correct"] += bool(rec["correct"])
        elif rt_ms is not None:
            meta["_rts"].append(rt_ms)
            meta["median_rt_ms"] = statistics.median(meta["_rts"])
        if s.done:
            meta.update(status="complete", finished=now)
        _write_json_atomic(folder / "session.json", {k: v for k, v in meta.items() if not k.startswith("_")})

    def summaries(self):
        rows, now = [], time.time()
        for p in sorted(self.root.glob("*/*/session.json")):  # shortcut: scans files per request; index if slow
            m = json.loads(p.read_text(encoding="utf-8"))
            last = m.get("updated") or m["started"]
            pro = m.get("prolific") or {}
            rows.append({"state": _state(m, now - last > self.abandon_after_s),
                         "user_id": m["user_id"], "run": m["run"],
                         "rated": f'{m["n_trials_done"]}/{m["n_trials"]}',
                         "attention_correct": m["attention_correct"], "attention_total": m["attention_total"],
                         "median_rt_ms": m["median_rt_ms"],
                         "duration_min": round(((m["finished"] or last) - m["started"]) / 60, 1),
                         "started": _fmt_time(m["started"]), "last_activity": _fmt_time(last),
                         "finished": _fmt_time(m["finished"]),
                         "prolific_study_id": pro.get("STUDY_ID", ""), "prolific_session_id": pro.get("SESSION_ID", ""),
                         "sid": m["sid"]})
        return rows


def _state(m, idle) -> str:
    """pass / fail (finished, every check right or not), running, abandoned (no rating for abandon_after_min)."""
    if m["status"] == "complete":
        return "pass" if m["attention_correct"] == m["attention_total"] else "fail"
    return "abandoned" if idle else "running"


def _fmt_time(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def make_blueprint(typ: TypStudy, is_admin) -> Blueprint:
    """``is_admin(key) -> bool`` guards the monitoring routes."""
    bp = Blueprint("typ", __name__)

    @bp.before_app_request
    def entry_from_url():
        if request.path != "/":
            return None
        # Prolific entry: /?PROLIFIC_PID=..&STUDY_ID=..&SESSION_ID=.. -> /?sid=<pid>--run=<k>
        try:
            ids = prolific_ids(request.args)
        except ValueError as e:
            abort(400, str(e))
        if ids:
            pid = ids["PROLIFIC_PID"]
            with typ.lock:
                sid = assign_sid(typ.root, ids, "", lambda k: f"{pid}--run={k}", typ.used_runs)
            return redirect(f"{request.script_root}/?sid={sid}")
        # Potato's URL login only runs when the browser is not logged in yet, so a participant
        # opening the URL of their next session would land back in the previous one.
        sid = request.args.get("sid")
        if sid is None:
            return None
        if not parse_sid(sid):
            abort(400, "malformed session id; expected <user_id>--run=<k>")
        if session.get("username") not in (None, sid):
            session.clear()
        return None

    @bp.app_context_processor
    def exit_link():
        # templates/done.html is only reached by declining consent; Prolific participants go back
        # to Prolific with the no-consent code
        parsed = parse_sid(session.get("username") or "")
        code = typ.study["no_consent_code"]
        if parsed and is_prolific_user(parsed[0]) and code:
            return {"exit_url": completion_url(code)}
        return {}

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
        with meta["_lock"]:
            meta["_prev"] = time.time()  # page (re)load: the participant's clock restarts too
            return jsonify(typ.start_payload(s, meta))

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
        with meta["_lock"]:
            if s.done or i_trial != s.i_trial:
                # stale or repeated answer (double click, second tab): ignore it, tell the page where we are
                resp = typ.done_payload(meta) if s.done else {"done": False}
                return jsonify({**resp, "stale": True, "i_trial": s.i_trial}), 409
            if not s.valid_value(value):
                abort(400, "value outside the scale or off its step")
            typ.record(sid, s, meta, value, rt_ms, first_touch_ms)
            return jsonify({**(typ.done_payload(meta) if s.done else {"done": False}), "i_trial": s.i_trial})

    def admin_rows():
        if not is_admin(request.args.get("key")):
            abort(403, "admin key required: ?key=<contents of admin_api_key.txt>")
        return filter_rows(typ.summaries(), request.args)

    @bp.get("/typ/admin.csv")
    def admin_csv():
        rows = admin_rows()
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["state"])
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=sessions--{typ.study['study_id']}.csv"})

    @bp.get("/typ/admin")
    def admin():
        rows = admin_rows()
        return admin_page(f"Typicality study {typ.study['study_id']}", "/typ/admin", rows, typ.summaries(), request.args)

    return bp


# ---------------------------------------------------------------- monitoring page

STATES = ("running", "abandoned", "pass", "fail")


def filter_rows(rows, args):
    """Rows matching ?state=..&q=..&study=.. (q: substring of user_id or sid; study: Prolific STUDY_ID)."""
    state, q, study = args.get("state", ""), args.get("q", "").strip().lower(), args.get("study", "")
    return [r for r in rows
            if (not state or r["state"] == state)
            and (not q or q in r["user_id"].lower() or q in r["sid"].lower())
            and (not study or r["prolific_study_id"] == study)]


def admin_page(title, path, rows, all_rows, args):
    esc = lambda v: html.escape("" if v is None else str(v), quote=True)
    cols = list(rows[0]) if rows else []
    counts = {s: sum(r["state"] == s for r in all_rows) for s in STATES}
    state, q, study, refresh = args.get("state", ""), args.get("q", ""), args.get("study", ""), args.get("refresh", "")
    opt = lambda v, label, cur: f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(label)}</option>'
    studies = sorted({r["prolific_study_id"] for r in all_rows if r["prolific_study_id"]})
    query = "&".join(f"{k}={esc(args.get(k, ''))}" for k in ("key", "state", "q", "study"))
    return ADMIN_HTML.format(
        title=esc(title), path=path, key=esc(args.get("key", "")), query=query,
        refresh_meta='<meta http-equiv="refresh" content="30">' if refresh else "",
        counts=" · ".join(f"{s}: {counts[s]}" for s in STATES), n=len(rows), n_all=len(all_rows),
        state_opts=opt("", "all", state) + "".join(opt(s, s, state) for s in STATES),
        study_opts=opt("", "all", study) + "".join(opt(s, s, study) for s in studies),
        q=esc(q), checked=" checked" if refresh else "",
        head="".join(f"<th>{esc(c)}</th>" for c in cols),
        body="".join(f'<tr data-state="{esc(r["state"])}">' + "".join(f"<td>{esc(r[c])}</td>" for c in cols) + "</tr>"
                     for r in rows))


ADMIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>{refresh_meta}
<style>body{{font:14px system-ui,sans-serif;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left;white-space:nowrap}}form>*{{margin-right:10px}}
tr[data-state=fail]{{background:#fde2e2}}tr[data-state=pass]{{background:#e3f6e3}}
tr[data-state=running]{{background:#fff7d6}}tr[data-state=abandoned]{{background:#eee;color:#777}}</style></head><body>
<h2>{title}</h2>
<p>{counts} &nbsp;(showing {n} of {n_all})</p>
<form method="get" action="{path}"><input type="hidden" name="key" value="{key}">
<label>State <select name="state" onchange="this.form.submit()">{state_opts}</select></label>
<label>Prolific study <select name="study" onchange="this.form.submit()">{study_opts}</select></label>
<label>Search PID / sid <input name="q" value="{q}" size="28"></label>
<label><input type="checkbox" name="refresh" value="1"{checked} onchange="this.form.submit()"> auto-refresh (30 s)</label>
<button>Apply</button> <a href="{path}.csv?{query}">download CSV (these rows)</a></form>
<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
</body></html>"""
