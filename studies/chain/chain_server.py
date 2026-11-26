"""Flask blueprint that serves the chain to the jsPsych page and stores every answer.

Identity comes only from the Potato login (``session["username"]`` = the session id),
never from the request body. Storage, one folder per session:

    {output_root}/{study_id}/{user_id}/spec={spec}/session.json   summary, rewritten each trial
    {output_root}/{study_id}/{user_id}/spec={spec}/trials.jsonl   one line per answered trial (all n_chain chains)
    {output_root}/{study_id}/{user_id}/prolific.json              Prolific SESSION_ID -> sid (prolific.py)
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

from flask import Blueprint, Response, abort, jsonify, redirect, request, send_file, session

from chain_core import (SIDES, SKIP, Session, attention_shortfall, build_proposal, check_spec_assets, load_assets, parse_sid,
                        session_files, session_seed)
from prolific import assign_sid, completion_url, ids_for_sid, prolific_ids

log = logging.getLogger(__name__)

MAX_RT_MS = 24 * 3600 * 1000
INFO_SHEET = Path(__file__).resolve().parent / "surveyflow" / "information_sheet.pdf"
CONSENT_FILE = Path(__file__).resolve().parent / "surveyflow" / "consent.jsonl"


def _write_json_atomic(path: Path, obj):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class ChainStudy:
    def __init__(self, study: dict, specs: dict):
        self.study_id = study["study_id"]
        self.root = Path(study["output_root"]) / self.study_id
        self.specs = specs
        self.abandon_after_s = 60 * study["abandon_after_min"]
        self.assets, self.proposals = {}, {}
        for spec in specs.values():  # load everything at boot so a bad spec fails before anyone arrives
            self.assets[spec.name] = load_assets(study["assets_root"], spec.stimuli_name, spec.graph_name)
            check_spec_assets(spec, self.assets[spec.name])
            if attention_shortfall(spec, self.assets[spec.name]):
                log.warning(attention_shortfall(spec, self.assets[spec.name]))
            self.proposals[spec.name] = build_proposal(spec, self.assets[spec.name])
            for w in self.proposals[spec.name].check():
                log.warning("spec %s: %s", spec.name, w)
        self.sessions = {}   # sid -> (Session, meta dict)
        # guards self.sessions and prolific.json only; each session's answers use its own meta["_lock"],
        # so the fsync of one session's answer never holds up another session
        self.lock = threading.Lock()

    def folder(self, user_id, spec_name) -> Path:
        return self.root / user_id / f"spec={spec_name}"

    def get(self, sid):
        """The live session for this sid, created or rebuilt from disk on first use. Caller holds the lock."""
        if sid in self.sessions:
            return self.sessions[sid]
        parsed = parse_sid(sid)
        if not parsed:
            abort(400, "malformed session id")
        user_id, spec_name = parsed
        spec = self.specs.get(spec_name)
        if spec is None:
            abort(404, f"unknown spec {spec_name!r}")
        seed = session_seed(self.study_id, user_id, spec_name)
        s = Session(spec, self.assets[spec_name], self.proposals[spec_name], seed)
        folder = self.folder(user_id, spec_name)
        meta_path = folder / "session.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["spec"] != spec.__dict__ or meta["seed"] != seed:
                abort(409, "the spec changed since this session started; use a new user id or spec name")
            if meta["asset_sha256"] != self.assets[spec_name].sha256:
                log.warning("%s: asset files changed since the session started", sid)
            records = [json.loads(line) for line in (folder / "trials.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            s.replay(records, meta.get("skips", []))
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "trials.jsonl").touch()
            meta = {"study_id": self.study_id, "sid": sid, "user_id": user_id, "seed": seed,
                    "spec": spec.__dict__, "asset_sha256": self.assets[spec_name].sha256,
                    "prolific": ids_for_sid(self.root, user_id, sid),
                    "status": "in_progress", "started": time.time(), "updated": None, "finished": None,
                    "n_trials": s.n_trials, "n_trials_done": 0, "n_draw_done": 0,
                    "attention_correct": 0, "attention_done": 0, "attention_passed": None, "median_rt_ms": None,
                    "skips": []}   # skipped chain pairs: not draws, not in trials.jsonl
            records = []
            _write_json_atomic(meta_path, meta)
        meta["_lock"] = threading.Lock()   # one answer at a time per session; sessions run in parallel
        meta["_rts"] = [r["rt_ms"] for r in records if r["trial_type"] == "chain" and r["rt_ms"] is not None]
        self.sessions[sid] = (s, meta)
        return self.sessions[sid]

    def payload(self, s: Session, meta: dict):
        """The trial on screen plus, for each side, the trial it leads to (None = the session ends)."""
        brief = lambda t: None if t is None else {"i_trial": t["i_trial"], "seq": t["seq"],
                                                  "left": t["left_text"], "right": t["right_text"]}
        if s.done:  # the code is only ever sent to a session that has answered every trial
            code = s.spec.completion_code
            return {"done": True, "completion_code": code,
                    "redirect_url": completion_url(code) if meta.get("prolific") and code else None}
        return {"done": False, "n_trials": s.n_trials, "question": s.spec.question,
                "trial": brief(s.pending), "next": {side: brief(t) for side, t in s.peek().items()}}

    def record(self, sid, s: Session, meta: dict, side: str, rt_ms):
        now = time.time()
        folder = self.folder(meta["user_id"], meta["spec"]["name"])
        if side == SKIP and s.pending["trial_type"] == "chain":
            # a skipped pair is not a draw: it goes in session.json, not trials.jsonl
            event = s.skip()
            event.update(rt_ms=rt_ms, t_skipped=now)
            meta["skips"].append(event)
            meta["_prev"] = meta["updated"] = now
            _write_json_atomic(folder / "session.json", {k: v for k, v in meta.items() if not k.startswith("_")})
            return
        rec = s.choose(side)
        # the page shows the next pair before the server hears about the click, so the server
        # cannot time a trial itself; server_ms is the gap since the previous answer (or page load)
        prev = meta.get("_prev")
        meta["_prev"] = now
        rec.update(user_id=meta["user_id"], sid=sid, rt_ms=rt_ms,
                   server_ms=round((now - prev) * 1000) if prev else None,
                   t_prev=prev, t_answered=now)
        with open(folder / "trials.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        meta["n_trials_done"] += 1
        meta["updated"] = now
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
                        attention_passed=meta["attention_correct"] >= s.spec.attention_pass * s.spec.n_chain)
        _write_json_atomic(folder / "session.json", {k: v for k, v in meta.items() if not k.startswith("_")})

    def summaries(self):
        rows, now = [], time.time()
        for p in session_files(self.root):  # shortcut: scans files per request; index if slow
            m = json.loads(p.read_text(encoding="utf-8"))
            last = m.get("updated") or m["started"]
            pro, sp = m.get("prolific") or {}, m["spec"]
            rows.append({"state": _state(m, now - last > self.abandon_after_s),
                         "user_id": m["user_id"], "spec": m["spec"]["name"],
                         "draws": f'{m["n_draw_done"]}/{sp["n_draw"] * sp["n_chain"]} ({sp["n_chain"]} chains)',
                         "attention": f'{m["attention_correct"]}/{m["attention_done"]} '
                                      f'(need {sp["attention_pass"] * sp["n_chain"]}/{sp["n_attention"] * sp["n_chain"]})',
                         "median_rt_ms": m["median_rt_ms"], "skips": len(m.get("skips", [])),
                         "duration_min": round(((m["finished"] or last) - m["started"]) / 60, 1),
                         "started": _fmt_time(m["started"]), "last_activity": _fmt_time(last),
                         "finished": _fmt_time(m["finished"]),
                         "prolific_study_id": pro.get("STUDY_ID", ""), "prolific_session_id": pro.get("SESSION_ID", ""),
                         "sid": m["sid"]})
        return rows


def _state(m, idle) -> str:
    """pass / fail (finished, attention passed or not), running, abandoned (no answer for abandon_after_min)."""
    if m["status"] == "complete":
        return "pass" if m["attention_passed"] else "fail"
    return "abandoned" if idle else "running"


def _fmt_time(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def make_blueprint(chain: ChainStudy, is_admin) -> Blueprint:
    """``is_admin(key) -> bool`` guards the monitoring routes."""
    bp = Blueprint("chain", __name__)

    @bp.before_app_request
    def entry_from_url():
        if request.path != "/":
            return None
        # Prolific entry: /?PROLIFIC_PID=..&STUDY_ID=..&SESSION_ID=..&spec=.. -> /?sid=<pid>--spec=<spec>;
        # one session per participant and spec, so a new Prolific SESSION_ID resumes the same session
        try:
            ids = prolific_ids(request.args)
        except ValueError as e:
            abort(400, str(e))
        if ids:
            spec = request.args.get("spec", "")
            if spec not in chain.specs:
                abort(404, f"unknown spec {spec!r} (the study URL needs &spec=<spec name>)")
            pid = ids["PROLIFIC_PID"]
            with chain.lock:
                sid = assign_sid(chain.root, ids, spec, lambda k: f"{pid}--spec={spec}", lambda user: [])
            return redirect(f"{request.script_root}/?sid={sid}")
        # Potato's URL login only runs when the browser is not logged in yet, so a participant
        # opening the URL of their next session would land back in the previous one.
        # A different ?sid= on the landing page therefore logs the old session out first.
        sid = request.args.get("sid")
        if sid and session.get("username") not in (None, sid):
            session.clear()
        return None

    @bp.get("/information_sheet.pdf")
    def information_sheet():
        # the full Information Sheet & Consent Form linked from the consent page; replace the file to update it
        if not INFO_SHEET.is_file():
            abort(404)
        return send_file(INFO_SHEET, mimetype="application/pdf", max_age=0)

    @bp.get("/chain/consent_text")
    def consent_text():
        # the consent page's text (its display blocks, not the checkbox), re-read on every request so it
        # always matches the consent page; the task page shows it behind the "home" icon
        blocks = [json.loads(line) for line in CONSENT_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
        return jsonify({"html": "".join(b.get("description", "") for b in blocks if b.get("annotation_type") == "pure_display")})

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
        with meta["_lock"]:
            meta["_prev"] = time.time()  # page (re)load: the participant's clock restarts too
            return jsonify(chain.payload(s, meta))

    @bp.post("/chain/choose")
    def choose():
        sid = current_sid()
        body = request.get_json(silent=True) or {}
        side, seq, rt_ms = body.get("side"), body.get("seq"), body.get("rt_ms")
        if side not in SIDES + (SKIP,) or type(seq) is not int:
            abort(400, "need side in ('left','right','skip') and an integer seq")
        if rt_ms is not None and (type(rt_ms) not in (int, float) or not math.isfinite(rt_ms) or not 0 <= rt_ms <= MAX_RT_MS):
            abort(400, "rt_ms must be a number of milliseconds")
        with chain.lock:
            s, meta = chain.get(sid)
        with meta["_lock"]:
            if s.done or seq != s.pending["seq"]:
                # stale or repeated answer (double click, second tab): ignore it and resend the current state
                resp = chain.payload(s, meta)
                resp["stale"] = True
                return jsonify(resp), 409
            chain.record(sid, s, meta, side, rt_ms)
            return jsonify(chain.payload(s, meta))

    def admin_rows():
        if not is_admin(request.args.get("key")):
            abort(403, "admin key required: ?key=<contents of admin_api_key.txt>")
        return filter_rows(chain.summaries(), request.args)

    @bp.get("/chain/admin.csv")
    def admin_csv():
        rows = admin_rows()
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["state"])
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=sessions--{chain.study_id}.csv"})

    @bp.get("/chain/admin")
    def admin():
        rows = admin_rows()
        return admin_page(f"Chain study {chain.study_id}", "/chain/admin", rows, chain.summaries(), request.args)

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
