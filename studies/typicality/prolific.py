"""Prolific entry and exit, with no web framework.

Entry: Prolific opens  /?PROLIFIC_PID=<pid>&STUDY_ID=<id>&SESSION_ID=<id>[&spec=<spec>].
The server turns that into one of our session ids and redirects to /?sid=<sid>, so the rest of
the app (Potato's URL login, storage, resume) is unchanged. Each Prolific SESSION_ID gets its
own session number k (chain=<k> or run=<k>); the same SESSION_ID always maps back to the
same sid, so a reload resumes.

    {root}/{PROLIFIC_PID}/prolific.json   {SESSION_ID: {"PROLIFIC_PID", "STUDY_ID", "SESSION_ID", "scope", "k", "sid", "arrived"}}

Exit: the last screen sends the participant to Prolific with a completion code.
"""

import json
import os
import re
import time
from pathlib import Path

PROLIFIC_KEYS = ("PROLIFIC_PID", "STUDY_ID", "SESSION_ID")
ID_RE = re.compile(r"[0-9a-f]{24}")      # Prolific ids are 24 lowercase hex characters
COMPLETE_URL = "https://app.prolific.com/submissions/complete?cc={code}"


def prolific_ids(args) -> dict | None:
    """The three Prolific ids from the query string; None if PROLIFIC_PID is absent.

    Raises ValueError if any of them is missing or malformed."""
    if "PROLIFIC_PID" not in args:
        return None
    ids = {k: args.get(k, "") for k in PROLIFIC_KEYS}
    bad = [k for k, v in ids.items() if not ID_RE.fullmatch(v)]
    if bad:
        raise ValueError(f"malformed or missing Prolific id(s): {', '.join(bad)}")
    return ids


def is_prolific_user(user_id: str) -> bool:
    return bool(ID_RE.fullmatch(user_id))


def completion_url(code: str) -> str:
    return COMPLETE_URL.format(code=code)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def assign_sid(root: Path, ids: dict, scope: str, make_sid, used) -> str:
    """The session id for this Prolific session, created on first arrival. Caller holds the study lock.

    scope:        what session numbers count within (the spec name, or "" for one-task studies).
    make_sid:     k -> sid.
    used:         user_id -> session numbers already used in this scope (from the session folders),
                  so a number started from a ?sid= test link is never reused.
    """
    pid = ids["PROLIFIC_PID"]
    path = root / pid / "prolific.json"
    known = _read(path)
    entry = known.get(ids["SESSION_ID"])
    if entry:
        return entry["sid"]
    taken = set(used(pid)) | {e["k"] for e in known.values() if e["scope"] == scope}
    k = max(taken, default=0) + 1
    known[ids["SESSION_ID"]] = {**ids, "scope": scope, "k": k, "sid": make_sid(k), "arrived": time.time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return known[ids["SESSION_ID"]]["sid"]


def ids_for_sid(root: Path, user_id: str, sid: str) -> dict | None:
    """The Prolific ids a sid was created for, or None (test links, ?sid= entry)."""
    for e in _read(root / user_id / "prolific.json").values():
        if e["sid"] == sid:
            return {k: e[k] for k in PROLIFIC_KEYS}
    return None
