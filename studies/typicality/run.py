"""Start the typicality study: prepare inputs from study.yaml, then Potato with three additions.

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

    from flask import abort, request, session
    from jinja2 import ChoiceLoader, FileSystemLoader
    from potato.flask_server import create_app

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

    app.run(host=study["host"], port=study["port"], threaded=True, use_reloader=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
