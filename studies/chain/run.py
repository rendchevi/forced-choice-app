"""Start the study: Potato (login, phases, export) plus the /chain/* routes.

    python run.py [sessions.yaml]

Prolific URL:     http://<host>:<port>/?PROLIFIC_PID=<pid>&STUDY_ID=<id>&SESSION_ID=<id>&spec=<spec>
Test URL:         http://<host>:<port>/?sid=<user_id>--spec=<spec>
Monitoring:       http://<host>:<port>/chain/admin?key=<contents of admin_api_key.txt>
"""

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(study_path="sessions.yaml"):
    os.chdir(ROOT)  # Potato resolves task_dir and every path in config.yaml from here
    sys.path.insert(0, str(ROOT))
    from jinja2 import ChoiceLoader, FileSystemLoader
    from potato.flask_server import create_app
    from potato.server_utils.admin_key import validate_admin_api_key
    from potato.server_utils.config_module import config as potato_config

    from chain_core import load_study
    from chain_server import ChainStudy, make_blueprint

    study, specs = load_study(study_path)
    chain = ChainStudy(study, specs)  # loads and checks every spec's assets before the server starts
    # create_app rebinds potato.flask_server.app, so the blueprint goes on the app it returns
    app = create_app(study["potato_config"])
    # each study has its own cookie: browsers share cookies across ports, so two studies on one
    # host would otherwise log each other out
    app.config["SESSION_COOKIE_NAME"] = "session_chain"
    # templates/done.html replaces Potato's completion page, which only declining consent reaches
    app.jinja_env.loader = ChoiceLoader([FileSystemLoader(str(ROOT / "templates")), app.jinja_env.loader])
    app.register_blueprint(make_blueprint(chain, lambda key: validate_admin_api_key(key, potato_config)))
    logging.getLogger(__name__).info("chain study %s: specs %s, data in %s", chain.study_id, list(specs), chain.root)
    from waitress import serve
    # one process, many threads: all session state lives in this process's memory
    serve(app, host=study["host"], port=study["port"], threads=study["threads"])


if __name__ == "__main__":
    main(*sys.argv[1:])
