"""Start the typicality study: prepare inputs from study.yaml, then Potato with four additions.

    python run.py [study.yaml]

Participant URL:  http://<host>:<port>/?sid=<user_id>--chain=<k>
Monitoring:       http://<host>:<port>/admin  (attention-check results, progress, export)
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# <user_id>--chain=<k>; user_id as in the chain study (single hyphens allowed, never "--")
SID_RE = re.compile(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*--chain=[0-9]{1,6}")


def main(study_path="study.yaml"):
    os.chdir(ROOT)  # Potato resolves task_dir and every path in config.yaml from here
    sys.path.insert(0, str(ROOT))
    import prepare
    study = prepare.main(study_path)

    import csv
    import json

    from flask import abort, request, session
    from jinja2 import ChoiceLoader, FileSystemLoader
    from potato.flask_server import create_app
    from potato.item_state_management import get_item_state_manager

    from order import even_order

    app = create_app("config.yaml")
    # 1. own cookie: browsers share cookies across ports, so the chain study on the same host
    #    would otherwise log this one out
    app.config["SESSION_COOKIE_NAME"] = "session_typicality"
    # 2. templates/done.html replaces Potato's completion page (its wording is fixed in the template)
    app.jinja_env.loader = ChoiceLoader([FileSystemLoader(str(ROOT / "templates")), app.jinja_env.loader])

    # 3. check the session id, and let a new ?sid= log the previous session out (Potato's URL
    #    login only runs when the browser is not logged in yet)
    @app.before_request
    def sid_from_url():
        sid = request.args.get("sid")
        if request.path != "/" or sid is None:
            return
        if not SID_RE.fullmatch(sid):
            abort(400, "malformed session id; expected <user_id>--chain=<k>")
        if session.get("username") not in (None, sid):
            session.clear()

    # 4. each participant's order: stimuli shuffled, attention items evenly spread. Potato hands out
    #    items a few at a time in its own random order, so this study assigns them itself: all at
    #    once, in the planned order, on the participant's first annotation page.
    attention_ids = [a["id"] for a in json.loads((ROOT / "data" / "attention.json").read_text(encoding="utf-8"))]
    with open(ROOT / "data" / "stimuli.csv", newline="", encoding="utf-8") as f:
        stimulus_ids = [r["stimulus_id"] for r in csv.DictReader(f) if r["stimulus_id"] not in attention_ids]
    ism = get_item_state_manager()

    def assign_in_planned_order(user_state):
        have = set(user_state.get_assigned_instance_ids())
        plan = even_order(stimulus_ids, attention_ids, f"{study['study_id']}\x1f{user_state.user_id}")
        todo = [iid for iid in plan if iid not in have]
        for iid in todo:
            user_state.assign_instance(ism.get_item(iid))
        return len(todo)

    ism.assign_instances_to_user = assign_in_planned_order  # shortcut: replaces a Potato internal; pinned to 2.14.0

    app.run(host=study["host"], port=study["port"], threaded=True, use_reloader=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
