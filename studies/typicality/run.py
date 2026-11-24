"""Start the typicality study: Potato (login, the jsPsych page) plus the /typ/* routes.

    python run.py [study.yaml]

Participant URL:  http://<host>:<port>/?sid=<user_id>--chain=<k>
Monitoring:       http://<host>:<port>/typ/admin?key=<contents of admin_api_key.txt>
"""

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(study_path="study.yaml"):
    os.chdir(ROOT)  # Potato resolves task_dir and every path in config.yaml from here
    sys.path.insert(0, str(ROOT))
    from potato.flask_server import create_app
    from potato.server_utils.admin_key import validate_admin_api_key
    from potato.server_utils.config_module import config as potato_config

    from typ_core import load_study
    from typ_server import TypStudy, make_blueprint

    study = load_study(study_path)
    typ = TypStudy(study)  # loads and checks the assets before the server starts
    # create_app rebinds potato.flask_server.app, so the blueprint goes on the app it returns
    app = create_app(study["potato_config"])
    # own cookie: browsers share cookies across ports, so the chain study on the same host
    # would otherwise log this one out
    app.config["SESSION_COOKIE_NAME"] = "session_typicality"
    app.register_blueprint(make_blueprint(typ, lambda key: validate_admin_api_key(key, potato_config)))
    logging.getLogger(__name__).info("typicality study %s: data in %s", study["study_id"], typ.root)
    app.run(host=study["host"], port=study["port"], threaded=True, use_reloader=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
